"""The durable outbox and explicit retries to the deduplicating test receiver."""

import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import UUID4, BaseModel, StringConstraints, ValidationError

from . import database
from .config import timeout_seconds
from .telemetry import stage, trace_id

logger = logging.getLogger("uvicorn.error.knowledge_desk.delivery")
TIMEOUT_SECONDS = timeout_seconds("KNOWLEDGE_DESK_N8N_TIMEOUT_SECONDS", 5)
SECRET_HEADER = "X-Knowledge-Desk-Secret"
TEST_PATH = "/webhook-test/knowledge-desk-request-created"
DEDUP_TEST_PATH = TEST_PATH + "-v2"


class Receipt(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["accepted"]
    event_id: UUID4
    request_id: UUID4
    execution_id: Annotated[str, StringConstraints(pattern=r"^[0-9]+$", max_length=64)]


class Delivery(BaseModel):
    event_id: UUID4
    request_id: UUID4
    status: Literal["pending", "delivered", "failed", "unknown"]
    reason: str | None
    receipt: Receipt | None
    updated_at: str
    can_retry: bool


def queue_created(connection: sqlite3.Connection, user_id: str, request_id: str) -> None:
    connection.execute(
        """INSERT INTO integration_events (event_id, request_id, user_id, type, status, updated_at)
           VALUES (?, ?, ?, 'request.created', 'pending', ?)""",
        (str(uuid4()), request_id, user_id, datetime.now(timezone.utc).isoformat()),
    )


def get_delivery(user_id: str, request_id: str) -> Delivery | None:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT event_id, request_id, status, reason, receipt, updated_at, receiver_key, attempt_until
               FROM integration_events
               WHERE user_id = ? AND request_id = ?""", (user_id, request_id),
        ).fetchone()
    if row is None:
        return None
    values = dict(row)
    if values["receipt"]:
        values["receipt"] = Receipt.model_validate_json(values["receipt"])
    receiver = retry_receiver(webhook_settings()) if row["status"] == "unknown" else None
    values["can_retry"] = bool(
        receiver and receiver == row["receiver_key"] and (row["attempt_until"] or 0) <= time.time()
    )
    return Delivery.model_validate(values)


def webhook_settings() -> tuple[str, str] | None:
    url = os.environ.get("KNOWLEDGE_DESK_N8N_TEST_URL", "").strip()
    secret = os.environ.get("KNOWLEDGE_DESK_N8N_SECRET", "").strip()
    try:
        parts = urlsplit(url)
        allowed_transport = parts.scheme == "https" or (
            parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (allowed_transport and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
                and parts.path in {TEST_PATH, DEDUP_TEST_PATH} and re.fullmatch(r"[!-~]{32,128}", secret)):
            return url, secret
    except ValueError:
        return None
    return None


def retry_receiver(settings: tuple[str, str] | None) -> str | None:
    if settings and urlsplit(settings[0]).path == DEDUP_TEST_PATH:
        return sha256(settings[0].encode()).hexdigest()
    return None


def claim(user_id: str, request_id: str, receiver: str | None, *, retry: bool = False) -> tuple[str, float] | None:
    with database.connect(immediate=True) as connection:
        now = time.time()
        attempt_until = now + TIMEOUT_SECONDS + 2
        # A crashed attempt becomes retryable when its lease expires.
        row = connection.execute(
            """UPDATE integration_events SET status = 'unknown', reason = 'attempt_started', updated_at = ?,
                   receiver_key = ?, attempt_until = ?
               WHERE user_id = ? AND request_id = ? AND status = ?
                   AND (status = 'pending' OR (receiver_key = ? AND COALESCE(attempt_until, 0) <= ?))
               RETURNING event_id""",
            (datetime.now(timezone.utc).isoformat(), receiver, attempt_until, user_id, request_id,
             "unknown" if retry else "pending", receiver, now),
        ).fetchone()
    return (row["event_id"], attempt_until) if row else None


async def post_event(url: str, secret: str, event: dict) -> httpx.Response:
    headers = {SECRET_HEADER: secret}
    if current := trace_id.get():
        headers["X-Trace-ID"] = current
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            return await client.post(url, json=event, headers=headers)


async def deliver(user_id: str, request_id: str, *, retry: bool = False) -> Delivery | None:
    settings = webhook_settings()
    if settings is None:
        return get_delivery(user_id, request_id)
    receiver = retry_receiver(settings)
    attempt = claim(user_id, request_id, receiver, retry=retry)
    if attempt is None:
        return get_delivery(user_id, request_id)
    event_id, attempt_until = attempt

    event = {"event_id": event_id, "request_id": request_id, "type": "request.created",
             "data": {"kind": "consultation", "test": True}}
    receipt = None
    status, reason = "unknown", "invalid_receipt"
    with stage("delivery", event_id=event_id, retry=retry, contract="v2" if receiver else "v1") as record:
        try:
            response = await post_event(*settings, event)
            if not response.is_success:
                code = response.status_code
                reason = f"http_{code}"
                # A rejected retry does not settle whether the previous attempt reached the receiver.
                if code == 409 or not (retry or (receiver and (code >= 500 or code == 408))):
                    status = "failed"
            else:
                candidate = Receipt.model_validate_json(response.content)
                if str(candidate.event_id) == event_id and str(candidate.request_id) == request_id:
                    receipt = candidate
                    status, reason = "delivered", None
        except (TimeoutError, httpx.TimeoutException):
            reason = "timeout"
        except (httpx.RequestError, httpx.InvalidURL):
            reason = "transport_error"
        except ValidationError:
            pass
        record.update(status=status, code=reason)

    with database.connect(immediate=True) as connection:
        connection.execute(
            """UPDATE integration_events SET status = ?, reason = ?, receipt = ?, updated_at = ?, attempt_until = NULL
               WHERE event_id = ? AND user_id = ? AND attempt_until = ?""",
            (status, reason, receipt.model_dump_json() if receipt else None,
             datetime.now(timezone.utc).isoformat(), event_id, user_id, attempt_until),
        )
    logger.info("Test delivery event_id=%s status=%s reason=%s", event_id, status, reason)
    return get_delivery(user_id, request_id)
