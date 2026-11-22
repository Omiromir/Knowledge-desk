"""Read and retry request delivery receipts."""

from fastapi import APIRouter, HTTPException

from ..integration_delivery import Delivery, deliver, get_delivery
from .dependencies import RequestHandler

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("/{request_id}/delivery", response_model=Delivery | None)
def request_delivery(request_id: str, service: RequestHandler):
    service.get(request_id)
    return get_delivery(service.user_id, request_id)


@router.post("/{request_id}/delivery/retry", response_model=Delivery)
async def retry_request_delivery(request_id: str, service: RequestHandler):
    service.get(request_id)
    current = get_delivery(service.user_id, request_id)
    if current is None:
        raise HTTPException(404, detail="Для этой заявки нет события доставки.")
    if current.status == "delivered":
        return current
    if not current.can_retry:
        raise HTTPException(409, detail="Сейчас повтор недоступен. Обновите статус доставки.")
    return await deliver(service.user_id, request_id, retry=True)
