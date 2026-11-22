"""Employment certificates, clarifications, and their live updates."""

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import UUID4
from starlette.concurrency import run_in_threadpool

from .. import certificate_notifications, certificates, postgres
from ..certificate_updates import card_topic, owner_topic
from ..certificates import CertificateRequest, ClarificationCreate, ClarificationText
from ..domain import NotFoundError
from ..models import RequestRecord
from ..services import RequestService
from .dependencies import Context, RequestHandler
from .request_views import request_detail

router = APIRouter(prefix="/requests", tags=["requests"])


@router.post("/employment-certificate", response_model=RequestRecord, response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED)
def create_certificate(payload: CertificateRequest, service: RequestHandler, context: Context):
    if service.role is None:
        raise HTTPException(401, "Войдите в Knowledge Desk.")
    return certificates.create(service.user_id, payload.purpose, email=context.email)


def certificate_event_scope(
    service: RequestHandler, request_id: UUID4 | None = None, mine: bool = False,
) -> tuple[str | None, str | None]:
    if mine:
        if service.role is None:
            raise HTTPException(401, "Войдите в Knowledge Desk.")
        if request_id is not None:
            raise HTTPException(422, "Выберите список или карточку заявки.")
        return owner_topic(service.user_id), service.user_id
    if request_id is None:
        if not certificates.can_issue(service.user_id, service.can_operate_requests):
            raise HTTPException(403, "Обновления очереди справок доступны кадровику.")
        return None, None
    record = certificate_record(str(request_id), service)
    if record.user_id != service.user_id:
        raise NotFoundError("Certificate request not found.")
    return card_topic(record.id), None


@router.get("/certificate-clarification-events", response_class=EventSourceResponse)
async def certificate_clarification_events(
    request: Request, scope: Annotated[tuple[str | None, str | None], Depends(certificate_event_scope)],
) -> AsyncIterator[ServerSentEvent]:
    topic, owner_id = scope
    async with aclosing(request.app.state.certificate_updates.stream(topic)) as events:
        async for event in events:
            if owner_id is not None:
                waiting = await run_in_threadpool(certificates.waiting_requests, owner_id, issuer=False)
                event = ServerSentEvent(
                    event=event.event, data={"revision": event.raw_data, "waiting": len(waiting)}, retry=event.retry,
                )
            yield event


@router.post("/{request_id}/certificate/clarifications", response_model=RequestRecord, response_model_exclude_none=True)
async def ask_certificate_clarification(request_id: str, clarification: ClarificationCreate, service: RequestHandler, request: Request):
    record = await run_in_threadpool(certificate_record, request_id, service)
    if not certificates.can_issue(service.user_id, service.can_operate_requests):
        raise HTTPException(403, "Запрашивать уточнения может только назначенный кадровик.")
    changed = await run_in_threadpool(
        certificates.ask_clarification, request_id, service.user_id, str(clarification.id), clarification.text,
        owner_id=record.user_id,
    )
    if changed and not postgres.database_url():
        request.app.state.certificate_updates.publish(card_topic(request_id))
        request.app.state.certificate_updates.publish(owner_topic(record.user_id))
    return await run_in_threadpool(certificate_detail_payload, request_id, service)


@router.put(
    "/{request_id}/certificate/clarifications/{clarification_id}/answer",
    response_model=RequestRecord, response_model_exclude_none=True,
)
async def answer_certificate_clarification(
    request_id: str, clarification_id: UUID4, clarification: ClarificationText, service: RequestHandler, request: Request,
):
    record = await run_in_threadpool(certificate_record, request_id, service)
    if record.user_id != service.user_id:
        raise NotFoundError("Clarification not found.")
    changed = await run_in_threadpool(
        certificates.answer_clarification, request_id, service.user_id, str(clarification_id), clarification.text,
    )
    if changed and not postgres.database_url():
        request.app.state.certificate_updates.publish()
        request.app.state.certificate_updates.publish(owner_topic(service.user_id))
    return await run_in_threadpool(certificate_detail_payload, request_id, service)


@router.get("/{request_id}/certificate/{variant}")
def read_certificate(request_id: str, variant: Literal["draft", "issued"], service: RequestHandler):
    issuer = certificates.can_issue(service.user_id, service.can_operate_requests)
    record = certificate_record(request_id, service)
    draft = variant == "draft"
    if (draft or record.user_id != service.user_id) and not issuer:
        raise NotFoundError("Certificate file not found.")
    content = certificates.download(request_id, service.user_id, draft=draft, issuer=issuer)
    extension = "docx" if draft else "pdf"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if draft else "application/pdf"
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="employment-certificate.{extension}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.put("/{request_id}/certificate/issued", response_model=RequestRecord, response_model_exclude_none=True)
async def issue_certificate(request_id: str, request: Request, service: RequestHandler, background: BackgroundTasks):
    if not certificates.can_issue(service.user_id, service.can_operate_requests):
        raise HTTPException(403, "Выдавать справки может только назначенный кадровик.")
    await run_in_threadpool(certificate_record, request_id, service)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise HTTPException(415, "Прикрепите подписанную справку в PDF.")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > certificates.MAX_PDF_BYTES:
            raise HTTPException(413, "Размер PDF должен быть не больше 5 МиБ.")
        data.extend(part)
    await run_in_threadpool(certificates.issue, request_id, service.user_id, bytes(data))
    background.add_task(certificate_notifications.deliver, request_id)
    return await run_in_threadpool(certificate_detail_payload, request_id, service)


def certificate_record(request_id: str, service: RequestService):
    if service.role is None:
        raise HTTPException(401, "Войдите в Knowledge Desk.")
    record = service.get(request_id)
    if record.kind != certificates.KIND:
        raise NotFoundError("Certificate request not found.")
    return record


def certificate_detail_payload(request_id: str, service: RequestService):
    return request_detail(service.get(request_id), service)
