"""One model proposal, one validated tool invocation, one tool result message."""

import asyncio
import logging
import os
from threading import BoundedSemaphore

from openai import APITimeoutError, AsyncOpenAI, OpenAIError, RateLimitError
from openai.types.responses import ResponseFunctionToolCall

from .summarizer import MODEL, TIMEOUT_SECONDS, SummaryError, provider_limit_error
from .telemetry import stage, usage_fields
from .tool_execution import Query, StrictModel, ToolContext, ToolExecution, execute_tool, tool_definitions
from .prompts import read_prompt

logger = logging.getLogger(__name__)
# shortcut: process-local quota in this local prototype; use a shared quota before publication.
_attempts = BoundedSemaphore(20)

INSTRUCTIONS = read_prompt("tools/assistant-v1.txt")


class ToolInput(StrictModel):
    message: Query


class ToolTurn(StrictModel):
    execution: ToolExecution | None = None
    message: str | None = None


async def run_tool_turn(message: str, context: ToolContext) -> ToolTurn:
    tools = tool_definitions(context)
    if not tools:
        raise SummaryError("forbidden_tool", "No tools are available to this user.")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SummaryError("not_configured", "The assistant model is not configured.")
    if not _attempts.acquire(blocking=False):
        raise SummaryError("local_limit", "Local assistant limit reached.")

    async with AsyncOpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0) as client:
        try:
            with stage("model", model=MODEL, schema="tool_proposal", version="v1") as record:
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    proposal = await client.responses.create(
                        model=MODEL, instructions=INSTRUCTIONS, input=message, tools=tools,
                        tool_choice="auto", parallel_tool_calls=False, max_output_tokens=512, store=False,
                    )
                record.update(usage_fields(proposal))
        except (APITimeoutError, TimeoutError):
            raise SummaryError("provider_timeout", "The assistant model did not respond in time.") from None
        except RateLimitError as exc:
            raise provider_limit_error(exc) from None
        except OpenAIError:
            raise SummaryError("provider_unavailable", "The assistant model is temporarily unavailable.") from None
        calls = [item for item in proposal.output if isinstance(item, ResponseFunctionToolCall)]
        if proposal.status != "completed" or len(calls) > 1:
            raise SummaryError("invalid_tool_proposal", "The assistant model returned an invalid proposal.")
        if not calls:
            return ToolTurn(message="Уточните, что искать. Для черновика заявки укажите имя и вопрос.")

        call = calls[0]
        execution = await execute_tool(call.name, call.arguments, context)
        try:
            with stage("model", model=MODEL, schema="tool_receipt", version="v1") as record:
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    receipt = await client.responses.create(
                        model=MODEL, instructions=INSTRUCTIONS, store=False, max_output_tokens=128,
                        tools=tools, tool_choice="none", parallel_tool_calls=False,
                        input=[
                            {"role": "user", "content": message},
                            {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},
                            {"type": "function_call_output", "call_id": call.call_id,
                             "output": execution.result.model_dump_json()},
                        ],
                    )
                record.update(usage_fields(receipt))
            if receipt.status != "completed":
                raise TimeoutError
        except (OpenAIError, TimeoutError):
            logger.info("Tool result model receipt unavailable")
            return ToolTurn(execution=execution, message="Модель не подтвердила получение результата. Результат сервера доступен ниже.")
    return ToolTurn(execution=execution)
