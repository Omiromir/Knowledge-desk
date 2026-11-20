from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import UUID4

from .. import database
from ..domain import RequestReplyConflict, RequestStatusConflict
from ..integration_delivery import Delivery, deliver, get_delivery
from ..models import RequestCreate, RequestRecord, RequestReplyCreate, RequestReplyText, RequestStatusUpdate
from ..services import RequestService
from .dependencies import Context, check_document_access, request_service

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
    return database.list_requests(service.scope)


@router.get("/{request_id}", response_model=RequestRecord, response_model_exclude_none=True)
def get_request(request_id: str, service: Service):
    return service.get(request_id)


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
    try:
        return service.change_status(request_id, **update.model_dump())
    except RequestStatusConflict as error:
        raise HTTPException(409, detail={"code": "request_status_conflict", "message": str(error)}) from None


@router.post("/{request_id}/replies", response_model=RequestRecord, response_model_exclude_none=True)
def reply_to_request(request_id: str, reply: RequestReplyCreate, service: Service):
    require_operator(service)
    try:
        return service.reply(request_id, str(reply.id), reply.text)
    except RequestReplyConflict as error:
        raise HTTPException(409, detail={"code": "request_reply_conflict", "message": str(error)}) from None


@router.patch("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def edit_reply(request_id: str, reply_id: UUID4, reply: RequestReplyText, service: Service):
    require_operator(service)
    return service.change_reply(request_id, str(reply_id), reply.text)


@router.delete("/{request_id}/replies/{reply_id}", response_model=RequestRecord, response_model_exclude_none=True)
def delete_reply(request_id: str, reply_id: UUID4, service: Service):
    require_operator(service)
    return service.change_reply(request_id, str(reply_id), None)


def require_operator(service: RequestService):
    if service.role not in (None, "operator"):
        raise HTTPException(403, "Принять заявку в работу и ответить может оператор.")
