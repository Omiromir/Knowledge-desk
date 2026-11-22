"""Send one notice per issued certificate, without sending its contents."""

import os
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import database, postgres
from .config import FRONTEND_ORIGIN
from .integration_delivery import Receipt, post_event
from .telemetry import stage

EVENT_TYPE = "certificate.issued"
WEBHOOK_PATHS = {"/webhook/knowledge-desk-certificate-issued", "/webhook-test/knowledge-desk-certificate-issued"}


class CertificateReceipt(Receipt):
    provider_email_id: UUID | None = None


def webhook_settings() -> tuple[str, str] | None:
    url = os.environ.get("KNOWLEDGE_DESK_N8N_CERTIFICATE_URL", "").strip()
    secret = os.environ.get("KNOWLEDGE_DESK_N8N_SECRET", "").strip()
    try:
        parts = urlsplit(url)
        loopback = parts.hostname in {"127.0.0.1", "localhost", "::1"}
        if ((parts.scheme == "https" or (parts.scheme == "http" and loopback))
                and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
                and parts.path in WEBHOOK_PATHS and re.fullmatch(r"[!-~]{32,128}", secret)):
            return url, secret
    except ValueError:
        pass
    return None


def scope(connection, request_id: str) -> None:
    if postgres.database_url():
        connection.execute("SELECT set_config('knowledge_desk.notification_request_id', ?, true)", (request_id,))


def prepare(connection, request_id: str, email: str) -> None:
    scope(connection, request_id)
    connection.execute(
        """INSERT INTO certificate_notifications (request_id, event_id, recipient, status)
           VALUES (?, ?, ?, 'waiting')""", (request_id, str(uuid4()), email),
    )


def queue(connection, request_id: str) -> None:
    scope(connection, request_id)
    connection.execute(
        "UPDATE certificate_notifications SET status = 'pending' WHERE request_id = ? AND status = 'waiting'",
        (request_id,),
    )


def claim(request_id: str):
    with database.connect(immediate=True) as connection:
        scope(connection, request_id)
        return connection.execute(
            """UPDATE certificate_notifications SET status = 'unknown'
               WHERE request_id = ? AND status = 'pending' RETURNING event_id, recipient""",
            (request_id,),
        ).fetchone()


def finish(request_id: str, status: str, provider_email_id: str | None) -> None:
    with database.connect(immediate=True) as connection:
        scope(connection, request_id)
        connection.execute(
            """UPDATE certificate_notifications SET status = ?, provider_email_id = COALESCE(provider_email_id, ?)
               WHERE request_id = ?""",
            (status, provider_email_id, request_id),
        )


def email_status(request_id: str) -> str | None:
    with database.connect() as connection:
        scope(connection, request_id)
        row = connection.execute(
            "SELECT status, email_status FROM certificate_notifications WHERE request_id = ?", (request_id,)
        ).fetchone()
    if row is None:
        return None
    return row["email_status"] or {
        "waiting": "pending", "pending": "pending", "unknown": "unknown",
        "failed": "not_delivered", "delivered": "accepted",
    }[row["status"]]


def record_email_event(email_id: str, event_id: str | None, status: str, occurred_at: str) -> None:
    with database.connect(immediate=True) as connection:
        if postgres.database_url():
            connection.execute("SELECT set_config('knowledge_desk.notification_email_id', ?, true)", (email_id,))
            connection.execute("SELECT set_config('knowledge_desk.notification_event_id', ?, true)", (event_id or "",))
        connection.execute(
            """UPDATE certificate_notifications
               SET provider_email_id = ?, email_status = ?, email_status_at = ?
               WHERE (provider_email_id = ? OR (
                   provider_email_id IS NULL AND event_id = ?
                   AND NOT EXISTS (SELECT 1 FROM certificate_notifications WHERE provider_email_id = ?)
               ))
                 AND status IN ('unknown', 'delivered')
                 AND (
                     email_status_at IS NULL OR
                     (email_status_at < ? AND (? <> 'delayed' OR email_status = 'delayed')) OR
                     (email_status_at = ? AND email_status = 'delayed' AND ? <> 'delayed')
                 )""",
            (email_id, status, occurred_at, email_id, event_id, email_id, occurred_at, status, occurred_at, status),
        )


async def deliver(request_id: str) -> None:
    settings = webhook_settings()
    if settings is None:
        return
    notification = await run_in_threadpool(claim, request_id)
    if notification is None:
        return
    event_id = notification["event_id"]
    event = {
        "event_id": event_id,
        "request_id": request_id,
        "type": EVENT_TYPE,
        "recipient": notification["recipient"],
        "request_url": f"{FRONTEND_ORIGIN}/#requests?id={request_id}",
    }
    status = "unknown"
    provider_email_id = None
    with stage("certificate_notification", event_id=event_id) as record:
        try:
            response = await post_event(*settings, event)
            if response.is_success:
                receipt = CertificateReceipt.model_validate_json(response.content)
                if str(receipt.event_id) == event_id and str(receipt.request_id) == request_id:
                    status = "delivered"
                    if receipt.provider_email_id is not None:
                        provider_email_id = str(receipt.provider_email_id)
                        record["provider_email_id"] = provider_email_id
            elif response.status_code in {400, 401, 403, 422}:
                status = "failed"
            record["http_status"] = response.status_code
        except (TimeoutError, httpx.RequestError, httpx.InvalidURL, ValidationError) as error:
            record["error_type"] = type(error).__name__
        record["status"] = status
    # A lost response can mean the email was already accepted. Leave it unknown for reconciliation.
    await run_in_threadpool(finish, request_id, status, provider_email_id)
