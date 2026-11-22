import base64
import binascii
import hmac
import json
import os
from datetime import UTC
from time import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from ..certificate_notifications import record_email_event

router = APIRouter()
MAX_BODY_BYTES = 64 * 1024
EVENT_STATUS = {
    "email.delivered": "delivered",
    "email.delivery_delayed": "delayed",
    "email.bounced": "not_delivered",
    "email.failed": "not_delivered",
    "email.suppressed": "not_delivered",
}


class EmailData(BaseModel):
    email_id: UUID
    tags: dict[str, str] | None = None


class EmailEvent(BaseModel):
    created_at: AwareDatetime
    data: EmailData


@router.post("/webhooks/resend", include_in_schema=False)
async def resend_webhook(request: Request):
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "").strip()
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True) if secret.startswith("whsec_") else b""
    except (ValueError, binascii.Error):
        key = b""
    if not key:
        raise HTTPException(503, "Webhook unavailable.")

    webhook_id = request.headers.get("svix-id", "")
    timestamp = request.headers.get("svix-timestamp", "")
    signature = request.headers.get("svix-signature", "")
    if (not webhook_id or not timestamp.isascii() or not timestamp.isdecimal()
            or len(timestamp) > 12 or abs(time() - int(timestamp)) > 300 or not signature):
        raise HTTPException(400, "Invalid webhook signature.")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook payload too large.")
        body.extend(chunk)

    # Svix signs the exact body bytes, including any JSON whitespace.
    signed = f"{webhook_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.digest(key, signed, "sha256"))
    if not any(
        hmac.compare_digest(candidate[3:].encode(), expected)
        for candidate in signature.split() if candidate.startswith("v1,")
    ):
        raise HTTPException(400, "Invalid webhook signature.")

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise ValueError
        status = EVENT_STATUS.get(payload["type"])
        if status is None:
            return {"ok": True}
        event = EmailEvent.model_validate(payload)
    except (ValueError, ValidationError, RecursionError):
        raise HTTPException(400, "Invalid webhook payload.") from None

    tag = (event.data.tags or {}).get("kd_certificate_event")
    try:
        event_id = str(UUID(tag)) if tag else None
    except ValueError:
        event_id = None
    occurred_at = event.created_at.astimezone(UTC).isoformat(timespec="microseconds")
    await run_in_threadpool(record_email_event, str(event.data.email_id), event_id, status, occurred_at)
    return {"ok": True}
