import asyncio
import logging
import os
from json import JSONDecodeError
from threading import BoundedSemaphore
from typing import TypeVar

from openai import APITimeoutError, AsyncOpenAI, OpenAIError, RateLimitError
from openai.types.responses import Response
from pydantic import BaseModel, ValidationError

from backend.rag_index.ingest import DEMO_CORPUS

from .config import timeout_seconds
from .eval_budget import BudgetExceeded, EvalBudget
from .models import GeneralRequestSummary, RequestSummary, SummaryInput
from .telemetry import stage, usage_fields
from .prompts import read_prompt

logger = logging.getLogger(__name__)

MODEL = "gpt-4.1-mini"
TIMEOUT_SECONDS = timeout_seconds("KNOWLEDGE_DESK_MODEL_TIMEOUT_SECONDS", 15)
MAX_OUTPUT_TOKENS = 256
MAX_ATTEMPTS = 5
_attempts = BoundedSemaphore(MAX_ATTEMPTS)
Result = TypeVar("Result", bound=BaseModel)

PROMPT_A = read_prompt("summary/education-a.txt")

PROMPT_B = read_prompt("summary/education-b.txt")

INSTRUCTIONS = PROMPT_B
GENERAL_INSTRUCTIONS = read_prompt("summary/general-v1.txt")


class SummaryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def provider_limit_error(error: RateLimitError) -> SummaryError:
    if error.code in {"insufficient_quota", "billing_hard_limit_reached"}:
        return SummaryError("provider_quota_exhausted", "Квота модели исчерпана. Обратитесь к владельцу сервиса.")
    return SummaryError("provider_rate_limit", "The provider is rate limiting requests. Retry later.")


async def request_summary_response(text: str, instructions: str, api_key: str) -> Response:
    return await request_structured_response(text, instructions, api_key, RequestSummary, MAX_OUTPUT_TOKENS)


async def request_structured_response(
    text: str, instructions: str, api_key: str, schema: type[BaseModel], max_output_tokens: int, *,
    model: str = MODEL, budget: EvalBudget | None = None,
) -> Response:
    schema_json = schema.model_json_schema()
    with stage("model", model=model, schema=schema.__name__, input_tokens=None, output_tokens=None) as record:
        timeout = min(TIMEOUT_SECONDS, budget.remaining_seconds()) if budget else TIMEOUT_SECONDS
        if budget:
            reservation = budget.reserve(model, text, instructions, schema_json, max_output_tokens)
            timeout = min(timeout, budget.remaining_seconds())
        async with AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0) as client:
            async with asyncio.timeout(timeout):
                response = await client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=text,
                    text={"format": {
                        "type": "json_schema",
                        "name": schema.__name__,
                        "schema": schema_json,
                        "strict": True,
                    }},
                    max_output_tokens=max_output_tokens,
                    store=False,
                )
        record.update(usage_fields(response))
        if budget:
            budget.settle(reservation, getattr(response, "usage", None), max_output_tokens)
        return response


def validate_summary_response(response: Response) -> RequestSummary:
    return validate_structured_response(response, RequestSummary, "invalid_summary")


def validate_structured_response(response: Response, schema: type[Result], invalid_code: str) -> Result:
    if not isinstance(response, Response) or not isinstance(getattr(response, "output", None), list):
        raise SummaryError(invalid_code, "The provider returned an invalid response.")
    try:
        for item in response.output:
            if item.type == "message" and any(part.type == "refusal" for part in item.content):
                raise SummaryError("provider_refused", "The provider refused this question.")
        if response.status != "completed":
            raise SummaryError(invalid_code, "The provider did not return a complete result.")
        return schema.model_validate_json(response.output_text)
    except (ValidationError, AttributeError, TypeError):
        raise SummaryError(invalid_code, "The provider returned an invalid result.") from None


async def summarize_request(text: str) -> RequestSummary | GeneralRequestSummary:
    text = SummaryInput(text=text).text
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SummaryError("not_configured", "Summary provider is not configured.")

    # shortcut: the budget resets per process; use project limits for a shared deployment.
    if not _attempts.acquire(blocking=False):
        raise SummaryError("local_limit", "Local summary limit reached.")

    if DEMO_CORPUS:
        return await complete_structured(text, INSTRUCTIONS, api_key, RequestSummary, MAX_OUTPUT_TOKENS, "invalid_summary")
    return await complete_structured(
        text, GENERAL_INSTRUCTIONS, api_key, GeneralRequestSummary, MAX_OUTPUT_TOKENS, "invalid_summary",
    )


async def complete_structured(
    text: str, instructions: str, api_key: str, schema: type[Result], max_output_tokens: int, invalid_code: str,
    *, model: str = MODEL, on_response=None, budget: EvalBudget | None = None,
) -> Result:
    try:
        response = await request_structured_response(text, instructions, api_key, schema, max_output_tokens,
                                                     model=model, budget=budget)
        if on_response is not None:
            on_response(response)
        return validate_structured_response(response, schema, invalid_code)
    except (APITimeoutError, TimeoutError):
        raise SummaryError("provider_timeout", f"The provider did not respond within {TIMEOUT_SECONDS:g} seconds.") from None
    except BudgetExceeded as exc:
        raise SummaryError(exc.code, str(exc)) from None
    except RateLimitError as exc:
        raise provider_limit_error(exc) from None
    except (JSONDecodeError, ValidationError):
        raise SummaryError(invalid_code, "The provider returned an unreadable response.") from None
    except OpenAIError as exc:
        logger.warning("Summary provider failure: %s (status=%s)", type(exc).__name__, getattr(exc, "status_code", None))
        raise SummaryError("provider_unavailable", "The provider could not prepare a result. Retry later.") from None
