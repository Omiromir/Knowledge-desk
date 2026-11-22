"""Validate and execute the two read-only tools available to the local assistant."""

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Awaitable, Callable, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .config import LOCAL_USER_ID
from .rag import retrieve_question, select_context
from .telemetry import stage

logger = logging.getLogger("uvicorn.error.knowledge_desk.tools")
TOOL_TIMEOUT = 3.0
MAX_TOOL_ARGUMENT_BYTES = 64 * 1024
ToolName = Literal["search_documents", "prepare_request"]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}


class SearchArguments(StrictModel):
    query: Query
    limit: Annotated[int, Field(ge=1, le=5)]


class RequestDraft(StrictModel):
    name: Name
    question: Question


class SearchFragment(StrictModel):
    chunk_id: Annotated[str, StringConstraints(min_length=1)]
    title: Annotated[str, StringConstraints(min_length=1)]
    text: Annotated[str, StringConstraints(min_length=1)]


class SearchData(StrictModel):
    fragments: Annotated[list[SearchFragment], Field(max_length=5)]


class ToolSuccess(StrictModel):
    status: Literal["success"] = "success"
    data: SearchData | RequestDraft
    attempts: int


class ToolError(StrictModel):
    status: Literal["error"] = "error"
    code: str
    reason: str
    retryable: bool
    attempts: int = 0


class ToolExecution(StrictModel):
    tool: ToolName | None
    result: Annotated[ToolSuccess | ToolError, Field(discriminator="status")]


@dataclass(frozen=True)
class ToolContext:
    user_id: str | None
    allowed_tools: frozenset[str]
    role: str | None = None
    email: str | None = None
    can_operate_requests: bool = False


async def search_documents(args: SearchArguments, *, user_id: str | None = None) -> dict:
    scope = {"user_id": user_id} if user_id else {}
    hits = await asyncio.to_thread(retrieve_question, args.query, **scope)
    fragments = select_context(hits)[:args.limit]
    return {"fragments": [item.model_dump(include={"chunk_id", "title", "text"}) for item in fragments]}


async def prepare_request(args: RequestDraft) -> dict:
    return args.model_dump()


@dataclass(frozen=True)
class Tool:
    description: str
    arguments: type[StrictModel]
    output: type[StrictModel]
    run: Callable[..., Awaitable[dict]]
    attempts: int = 1


TOOLS: dict[ToolName, Tool] = {
    "search_documents": Tool(
        "Найти до пяти фрагментов в базе по непустому запросу. Результат содержит chunk_id, название и текст.",
        SearchArguments, SearchData, search_documents, attempts=2,
    ),
    "prepare_request": Tool(
        "Подготовить черновик для просмотра человеком. Используй имя и вопрос из сообщения. Запись заявки не выполняется.",
        RequestDraft, RequestDraft, prepare_request,
    ),
}


def local_tool_context() -> ToolContext:
    # shortcut: local single-user mode; replace with authenticated server identity before publication.
    return ToolContext(user_id=LOCAL_USER_ID, allowed_tools=frozenset(TOOLS))


def tool_definitions(context: ToolContext) -> list[dict]:
    return [
        {"type": "function", "name": name, "description": tool.description, "strict": True,
         "parameters": tool.arguments.model_json_schema()}
        for name, tool in TOOLS.items() if context.user_id and name in context.allowed_tools
    ]


async def execute_tool(name: str, arguments: str, context: ToolContext, *, allow_retry: bool = True) -> ToolExecution:
    tool = TOOLS.get(name)
    with stage("tool_arguments", tool=name if tool else "unknown", contract="v1") as validation:
        if tool is None:
            validation["status"] = "unknown_tool"
            return ToolExecution(tool=None, result=ToolError(
                code="unknown_tool", reason="Этот инструмент недоступен.", retryable=False,
            ))
        if not context.user_id or name not in context.allowed_tools:
            validation["status"] = "forbidden_tool"
            return ToolExecution(tool=name, result=ToolError(
                code="forbidden_tool", reason="У текущего пользователя нет доступа к инструменту.", retryable=False,
            ))
        if len(arguments) > MAX_TOOL_ARGUMENT_BYTES or len(arguments.encode("utf-8", errors="surrogatepass")) > MAX_TOOL_ARGUMENT_BYTES:
            validation["status"] = "tool_arguments_too_large"
            return ToolExecution(tool=name, result=ToolError(
                code="tool_arguments_too_large", reason="Аргументы инструмента слишком велики.", retryable=False,
            ))
        try:
            args = tool.arguments.model_validate_json(arguments)
        except ValidationError:
            validation["status"] = "invalid_arguments"
            return ToolExecution(tool=name, result=ToolError(
                code="invalid_arguments", reason="Проверьте обязательные поля и ограничения инструмента.", retryable=False,
            ))

    attempts = tool.attempts if allow_retry else 1
    for attempt in range(1, attempts + 1):
        started = perf_counter()
        with stage("tool", tool=name, attempt=attempt, contract="v1") as record:
            try:
                async with asyncio.timeout(TOOL_TIMEOUT):
                    scope = {"user_id": context.user_id} if name == "search_documents" and context.role else {}
                    raw = await tool.run(args, **scope)
            except TimeoutError:
                result = ToolError(code="tool_timeout", reason="Инструмент не ответил за 3 секунды.",
                                   retryable=True, attempts=attempt)
            except Exception:
                result = ToolError(code="tool_failed", reason="Инструмент временно недоступен.",
                                   retryable=True, attempts=attempt)
            else:
                try:
                    data = tool.output.model_validate(raw)
                    if isinstance(args, SearchArguments) and isinstance(data, SearchData) and len(data.fragments) > args.limit:
                        raise ValueError("Search limit exceeded")
                except (ValidationError, ValueError):
                    result = ToolError(code="invalid_tool_output", reason="Инструмент вернул данные неожиданного формата.",
                                       retryable=False, attempts=attempt)
                else:
                    result = ToolSuccess(data=data, attempts=attempt)
            code = result.code if isinstance(result, ToolError) else result.status
            record["status"] = code
        logger.info("Tool outcome name=%s attempt=%d duration_ms=%.1f code=%s",
                    name, attempt, (perf_counter() - started) * 1000, code)
        if isinstance(result, ToolSuccess) or not result.retryable:
            break
    return ToolExecution(tool=name, result=result)
