"""Requests, reply history, and drafts for the operator queue."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from .. import certificates
from ..domain import RequestReplyConflict, RequestReplyDraftConflict, RequestStatusConflict
from ..models import (
    RequestCreate, RequestRecord, RequestReplyCreate, RequestReplyDraft, RequestReplyDraftWrite, RequestReplyText,
    RequestStatusUpdate,
)
from ..services import RequestService
from ..storage import requests as request_store
from .dependencies import Context, RequestHandler, check_document_access
from .profile import avatar_response
from .request_views import request_detail, request_avatar_image, with_message_authors

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post(
    "", response_model=RequestRecord, response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_request(request: RequestCreate, service: RequestHandler, context: Context):
    if request.document_id:
        check_document_access(request.document_id, context)
    return service.create(**request.model_dump())


@router.get("", response_model=list[RequestRecord], response_model_exclude_none=True)
def list_requests(service: RequestHandler):
    records = request_store.list_requests(service.scope)
    if not any(record.kind == certificates.KIND and record.status != "resolved" for record in records):
        return records
    waiting = certificates.waiting_requests(
        service.user_id, issuer=certificates.can_issue(service.user_id, service.can_operate_requests)
    )
    return [
        {**asdict(record), "certificate_awaiting_reply": True}
        if record.id in waiting and record.status != "resolved" else record
        for record in records
    ]


@router.get("/{request_id}", response_model=RequestRecord, response_model_exclude_none=True)
def get_request(request_id: str, service: RequestHandler):
    return request_detail(service.get(request_id), service)


@router.get("/{request_id}/avatars/{version}")
def request_avatar(request_id: str, version: UUID4, service: RequestHandler):
    return avatar_response(request_avatar_image(request_id, str(version), service))


def require_regular_request(request_id: str, service: RequestService) -> None:
    if service.get(request_id).kind == certificates.KIND:
        raise HTTPException(409, "Чтобы закрыть заявку на справку, прикрепите подписанный PDF.")


@router.patch("/{request_id}", response_model=RequestRecord, response_model_exclude_none=True)
def update_request_status(request_id: str, update: RequestStatusUpdate, service: RequestHandler):
    require_operator(service)
    require_regular_request(request_id, service)
    try:
        return with_message_authors(service.change_status(request_id, **update.model_dump()), service)
    except RequestStatusConflict as error:
        raise HTTPException(409, detail={"code": "request_status_conflict", "message": str(error)}) from None


@router.post("/{request_id}/replies", response_model=RequestRecord, response_model_exclude_none=True)
def reply_to_request(request_id: str, reply: RequestReplyCreate, service: RequestHandler):
    require_operator(service)
    require_regular_request(request_id, service)
    try:
        return with_message_authors(service.reply(request_id, str(reply.id), reply.text), service)
    except RequestReplyConflict as error:
        raise HTTPException(409, detail={"code": "request_reply_conflict", "message": str(error)}) from None


@router.get("/{request_id}/reply-draft", response_model=RequestReplyDraft | None)
def get_reply_draft(request_id: str, service: RequestHandler):
    require_draft_operator(service)
    return request_store.get_reply_draft(request_id, service.user_id)


@router.put("/{request_id}/reply-draft", response_model=RequestReplyDraft | None)
def put_reply_draft(request_id: str, draft: RequestReplyDraftWrite, service: RequestHandler):
    require_draft_operator(service)
    try:
        return request_store.save_reply_draft(
            request_id, service.user_id, draft.text, str(draft.revision),
            str(draft.expected_revision) if draft.expected_revision else None,
        )
    except RequestReplyDraftConflict:
        raise HTTPException(409, detail={"code": "reply_draft_changed"}) from None


def require_draft_operator(service: RequestService):
    if not service.can_operate_requests:
        raise HTTPException(403, "Черновики ответов доступны операторам.")


@router.patch("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def edit_reply(request_id: str, reply_id: UUID4, reply: RequestReplyText, service: RequestHandler):
    require_operator(service)
    return with_message_authors(service.change_reply(request_id, str(reply_id), reply.text), service)


@router.delete("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def delete_reply(request_id: str, reply_id: UUID4, service: RequestHandler):
    require_operator(service)
    return with_message_authors(service.change_reply(request_id, str(reply_id), None), service)


def require_operator(service: RequestService):
    if service.role is not None and not service.can_operate_requests:
        raise HTTPException(403, "Принять заявку в работу и ответить может оператор.")
