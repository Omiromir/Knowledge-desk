from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import UUID4, Field
from starlette.concurrency import run_in_threadpool

from .. import consultation_graph, consultation_store, integration_delivery, rag
from ..rate_limit import limit_expensive
from ..summarizer import SummaryError
from ..tool_execution import RequestDraft, SearchFragment, StrictModel, ToolContext
from .dependencies import Context

router = APIRouter(prefix="/consultations", tags=["consultations"])
VersionNumber = Annotated[int, Field(gt=0)]


class Version(StrictModel):
    version: VersionNumber


class VersionedDraft(RequestDraft):
    version: VersionNumber


class DraftView(VersionedDraft):
    draft_id: str
    status: Literal["draft", "awaiting_confirmation", "submitted", "failed", "cancelled"]
    stop_code: str | None
    request_id: str | None
    sources: list[SearchFragment]
    delivery: integration_delivery.Delivery | None = None


def owner(context: ToolContext) -> str:
    if not context.user_id or not {"search_documents", "prepare_request"} <= context.allowed_tools:
        raise HTTPException(403, detail={"code": "forbidden_consultation"})
    return context.user_id


def visible_sources(draft: dict, context: ToolContext) -> dict:
    if not context.role:
        return draft
    sources = []
    for item in draft["sources"]:
        try:
            current = rag.get_source(item["chunk_id"], user_id=context.user_id)
        except SummaryError:
            continue
        sources.append(current.model_dump(include={"chunk_id", "title", "text"}))
    return {**draft, "sources": sources}


@router.post("", response_model=DraftView, status_code=201)
async def create(data: RequestDraft, context: Context):
    return consultation_graph.view(consultation_store.create_draft(owner(context), data.name, data.question))


@router.get("/{draft_id}", response_model=DraftView)
async def read(draft_id: UUID4, context: Context):
    user_id = owner(context)
    draft = await consultation_graph.read_draft(user_id, str(draft_id))
    if draft["request_id"]:
        draft["delivery"] = integration_delivery.get_delivery(user_id, draft["request_id"])
    return await run_in_threadpool(visible_sources, draft, context)


@router.patch("/{draft_id}", response_model=DraftView)
async def edit(draft_id: UUID4, data: VersionedDraft, context: Context):
    user_id = owner(context)
    async with consultation_graph.draft_lock(str(draft_id)):
        return consultation_graph.view(consultation_store.revise_draft(user_id, str(draft_id), data.version, data.name, data.question))


@router.post("/{draft_id}/prepare", response_model=DraftView)
async def prepare(draft_id: UUID4, data: Version, context: Context, request: Request):
    user_id = owner(context)
    limit_expensive(request, user_id)
    draft = await consultation_graph.start_preparation(context, user_id, str(draft_id), data.version)
    return await run_in_threadpool(visible_sources, draft, context)


@router.post("/{draft_id}/confirm", response_model=DraftView)
async def confirm(draft_id: UUID4, data: VersionedDraft, context: Context):
    user_id = owner(context)
    draft = await consultation_graph.confirm_draft(context, user_id, str(draft_id), data.version, data)
    if draft["request_id"]:
        draft["delivery"] = await integration_delivery.deliver(user_id, draft["request_id"])
    return await run_in_threadpool(visible_sources, draft, context)


@router.post("/{draft_id}/cancel", response_model=DraftView)
async def cancel(draft_id: UUID4, data: Version, context: Context):
    draft = await consultation_graph.cancel_draft(owner(context), str(draft_id), data.version)
    return await run_in_threadpool(visible_sources, draft, context)
