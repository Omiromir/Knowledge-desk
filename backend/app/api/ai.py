from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..models import GeneralRequestSummary, RequestSummary, SummaryInput
from ..rate_limit import limit_expensive
from ..rag import AskInput, AskResult, SourceExcerpt, answer_question, get_source, retrieve_question, select_context
from ..summarizer import SummaryError, summarize_request
from ..tool_assistant import ToolInput, ToolTurn, run_tool_turn
from .dependencies import Context

router = APIRouter(tags=["ai"])

ERROR_STATUSES = {
    "not_configured": 503,
    "local_limit": 429,
    "provider_rate_limit": 429,
    "provider_quota_exhausted": 429,
    "provider_timeout": 504,
    "index_unavailable": 503,
    "source_not_found": 404,
    "forbidden_tool": 403,
}


def model_http_error(exc: SummaryError) -> HTTPException:
    return HTTPException(ERROR_STATUSES.get(exc.code, 502), {"code": exc.code, "message": str(exc)})


@router.post("/ai/summary", response_model=RequestSummary | GeneralRequestSummary)
async def prepare_summary(payload: SummaryInput, context: Context, request: Request) -> RequestSummary | GeneralRequestSummary:
    limit_expensive(request, context.user_id)
    try:
        return await summarize_request(payload.text)
    except SummaryError as exc:
        raise model_http_error(exc) from None


@router.post("/ask", response_model=AskResult, response_model_exclude_none=True)
async def ask(payload: AskInput, context: Context, request: Request) -> AskResult:
    limit_expensive(request, context.user_id)
    try:
        scope = {"user_id": context.user_id} if context.role else {}
        hits = await run_in_threadpool(retrieve_question, payload.question, **scope)
        return await answer_question(payload.question, select_context(hits))
    except SummaryError as exc:
        raise model_http_error(exc) from None


@router.get("/ask/sources/{chunk_id}", response_model=SourceExcerpt, response_model_exclude_none=True)
def source(chunk_id: str, context: Context) -> SourceExcerpt:
    try:
        scope = {"user_id": context.user_id} if context.role else {}
        return get_source(chunk_id, **scope)
    except SummaryError as exc:
        raise model_http_error(exc) from None


@router.post("/ai/tools", response_model=ToolTurn)
async def assistant_tools(payload: ToolInput, context: Context, request: Request) -> ToolTurn:
    limit_expensive(request, context.user_id)
    try:
        return await run_tool_turn(payload.message, context)
    except SummaryError as exc:
        raise model_http_error(exc) from None
