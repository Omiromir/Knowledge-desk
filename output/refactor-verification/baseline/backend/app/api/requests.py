from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import UUID4
from starlette.concurrency import run_in_threadpool

from .. import certificate_notifications, certificates, database, member_profiles, postgres
from ..certificate_updates import card_topic, owner_topic
from ..certificates import CertificateRequest, ClarificationCreate, ClarificationText
from ..domain import NotFoundError, RequestRecord as StoredRequest, RequestReplyConflict, RequestReplyDraftConflict, RequestStatusConflict
from ..integration_delivery import Delivery, deliver, get_delivery
from ..models import (
    RequestCreate, RequestRecord, RequestReplyCreate, RequestReplyDraft, RequestReplyDraftWrite, RequestReplyText,
    RequestStatusUpdate,
)
from ..services import RequestService
from .dependencies import Context, check_document_access, request_service
from .profile import avatar_response

router = APIRouter(prefix="/requests", tags=["requests"])
Service = Annotated[RequestService, Depends(request_service)]


@router.post(
    "", response_model=RequestRecord, response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_request(request: RequestCreate, service: Service, context: Context):
    if request.document_id:
        check_document_access(request.document_id, context)
    return service.create(**request.model_dump())


@router.get("", response_model=list[RequestRecord], response_model_exclude_none=True)
def list_requests(service: Service):
    records = database.list_requests(service.scope)
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


@router.post("/employment-certificate", response_model=RequestRecord, response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED)
def create_certificate(payload: CertificateRequest, service: Service, context: Context):
    if service.role is None:
        raise HTTPException(401, "Войдите в Knowledge Desk.")
    return certificates.create(service.user_id, payload.purpose, email=context.email)


def certificate_event_scope(
    service: Service, request_id: UUID4 | None = None, mine: bool = False,
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


@router.get("/{request_id}", response_model=RequestRecord, response_model_exclude_none=True)
def get_request(request_id: str, service: Service):
    record = service.get(request_id)
    if record.kind == certificates.KIND:
        owner = record.user_id == service.user_id and service.role is not None
        issuer = certificates.can_issue(service.user_id, service.can_operate_requests)
        can_download = owner or issuer
        history = certificates.clarifications(request_id, service.user_id, issuer=issuer) if can_download else []
        awaiting_reply = any(entry["answered_at"] is None for entry in history)
        return {
            **(with_message_authors(record, service, history) if can_download else asdict(record)),
            "certificate_owned": owner or None,
            "certificate_downloadable": can_download,
            "certificate_awaiting_reply": awaiting_reply or None,
            "certificate_can_answer": (awaiting_reply and owner) or None,
            "certificate_email_status": (
                certificate_notifications.email_status(request_id) if can_download and record.status == "resolved" else None
            ),
        }
    return with_message_authors(record, service)


def participant_profiles(record: StoredRequest, history: list[dict]):
    participants = {record.user_id}
    participants.update(reply.author_id for reply in record.replies or () if reply.author_id)
    participants.update(entry["asked_by"] for entry in history)
    return member_profiles.read_participants(participants)


def with_message_authors(record: StoredRequest, service: RequestService, history: list[dict] | None = None):
    if service.role is None:
        return record
    history = history or []
    profiles = participant_profiles(record, history)

    def author(user_id: str | None):
        profile = profiles.get(user_id, {})
        name, avatar = profile.get("display_name"), profile.get("avatar_version")
        own = user_id == service.user_id
        if name or avatar or own:
            return {"display_name": name or None, "avatar_version": avatar, "is_self": own or None}
        return None

    return {
        **asdict(record),
        "author": author(record.user_id),
        "replies": [{**asdict(reply), "author": author(reply.author_id)} for reply in record.replies or ()] or None,
        "certificate_clarifications": [
            {
                **entry,
                "question_author": author(entry["asked_by"]),
                "answer_author": author(record.user_id) if entry["answer"] else None,
            }
            for entry in history
        ] or None,
    }


@router.get("/{request_id}/avatars/{version}")
def request_avatar(request_id: str, version: UUID4, service: Service):
    if service.role is None:
        raise NotFoundError("Request avatar not found.")
    record = service.get(request_id)
    history = []
    if record.kind == certificates.KIND:
        issuer = certificates.can_issue(service.user_id, service.can_operate_requests)
        if record.user_id != service.user_id and not issuer:
            raise NotFoundError("Request avatar not found.")
        history = certificates.clarifications(request_id, service.user_id, issuer=issuer)
    for user_id, profile in participant_profiles(record, history).items():
        if profile["avatar_version"] == str(version):
            return avatar_response(member_profiles.read_avatar(user_id, str(version)))
    raise NotFoundError("Request avatar not found.")


@router.post("/{request_id}/certificate/clarifications", response_model=RequestRecord, response_model_exclude_none=True)
async def ask_certificate_clarification(request_id: str, clarification: ClarificationCreate, service: Service, request: Request):
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
    return await run_in_threadpool(get_request, request_id, service)


@router.put(
    "/{request_id}/certificate/clarifications/{clarification_id}/answer",
    response_model=RequestRecord, response_model_exclude_none=True,
)
async def answer_certificate_clarification(
    request_id: str, clarification_id: UUID4, clarification: ClarificationText, service: Service, request: Request,
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
    return await run_in_threadpool(get_request, request_id, service)


@router.get("/{request_id}/certificate/{variant}")
def read_certificate(request_id: str, variant: Literal["draft", "issued"], service: Service):
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
async def issue_certificate(request_id: str, request: Request, service: Service, background: BackgroundTasks):
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
    return await run_in_threadpool(get_request, request_id, service)


def certificate_record(request_id: str, service: RequestService):
    if service.role is None:
        raise HTTPException(401, "Войдите в Knowledge Desk.")
    record = service.get(request_id)
    if record.kind != certificates.KIND:
        raise NotFoundError("Certificate request not found.")
    return record


def require_regular_request(request_id: str, service: RequestService) -> None:
    if service.get(request_id).kind == certificates.KIND:
        raise HTTPException(409, "Чтобы закрыть заявку на справку, прикрепите подписанный PDF.")


@router.get("/{request_id}/delivery", response_model=Delivery | None)
def request_delivery(request_id: str, service: Service):
    service.get(request_id)
    return get_delivery(service.user_id, request_id)


@router.post("/{request_id}/delivery/retry", response_model=Delivery)
async def retry_request_delivery(request_id: str, service: Service):
    service.get(request_id)
    current = get_delivery(service.user_id, request_id)
    if current is None:
        raise HTTPException(404, detail="Для этой заявки нет события доставки.")
    if current.status == "delivered":
        return current
    if not current.can_retry:
        raise HTTPException(409, detail="Сейчас повтор недоступен. Обновите статус доставки.")
    return await deliver(service.user_id, request_id, retry=True)


@router.patch("/{request_id}", response_model=RequestRecord, response_model_exclude_none=True)
def update_request_status(request_id: str, update: RequestStatusUpdate, service: Service):
    require_operator(service)
    require_regular_request(request_id, service)
    try:
        return with_message_authors(service.change_status(request_id, **update.model_dump()), service)
    except RequestStatusConflict as error:
        raise HTTPException(409, detail={"code": "request_status_conflict", "message": str(error)}) from None


@router.post("/{request_id}/replies", response_model=RequestRecord, response_model_exclude_none=True)
def reply_to_request(request_id: str, reply: RequestReplyCreate, service: Service):
    require_operator(service)
    require_regular_request(request_id, service)
    try:
        return with_message_authors(service.reply(request_id, str(reply.id), reply.text), service)
    except RequestReplyConflict as error:
        raise HTTPException(409, detail={"code": "request_reply_conflict", "message": str(error)}) from None


@router.get("/{request_id}/reply-draft", response_model=RequestReplyDraft | None)
def get_reply_draft(request_id: str, service: Service):
    require_draft_operator(service)
    return database.get_reply_draft(request_id, service.user_id)


@router.put("/{request_id}/reply-draft", response_model=RequestReplyDraft | None)
def put_reply_draft(request_id: str, draft: RequestReplyDraftWrite, service: Service):
    require_draft_operator(service)
    try:
        return database.save_reply_draft(
            request_id, service.user_id, draft.text, str(draft.revision),
            str(draft.expected_revision) if draft.expected_revision else None,
        )
    except RequestReplyDraftConflict:
        raise HTTPException(409, detail={"code": "reply_draft_changed"}) from None


def require_draft_operator(service: RequestService):
    if not service.can_operate_requests:
        raise HTTPException(403, "Черновики ответов доступны операторам.")


@router.patch("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def edit_reply(request_id: str, reply_id: UUID4, reply: RequestReplyText, service: Service):
    require_operator(service)
    return with_message_authors(service.change_reply(request_id, str(reply_id), reply.text), service)


@router.delete("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def delete_reply(request_id: str, reply_id: UUID4, service: Service):
    require_operator(service)
    return with_message_authors(service.change_reply(request_id, str(reply_id), None), service)


def require_operator(service: RequestService):
    if service.role is not None and not service.can_operate_requests:
        raise HTTPException(403, "Принять заявку в работу и ответить может оператор.")
