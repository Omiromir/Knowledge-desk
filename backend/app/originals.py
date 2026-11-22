"""Serve immutable PDF originals through a bucket-scoped Supabase Auth account."""

import logging
import os
import re
from hashlib import sha256
from threading import Lock
from time import monotonic
from uuid import uuid4

import httpx
from fastapi import HTTPException

from . import auth, database, postgres

BUCKET = "knowledge-desk-originals"
logger = logging.getLogger(__name__)
_token_lock = Lock()
_token = None


class StorageError(Exception):
    pass


def storage_token() -> str:
    global _token
    email = os.environ.get("KNOWLEDGE_DESK_STORAGE_EMAIL", "")
    password = os.environ.get("KNOWLEDGE_DESK_STORAGE_PASSWORD", "")
    if not email or not password:
        raise StorageError("PDF storage is not configured")
    credentials = auth.settings(), email, password
    with _token_lock:
        if _token and _token[0] == credentials and _token[2] > monotonic():
            return _token[1]
        try:
            data = auth.provider("token?grant_type=password", payload={"email": email, "password": password})
            token, ttl = data["access_token"], int(data["expires_in"])
            if not isinstance(token, str) or not token or ttl <= 0:
                raise ValueError("Invalid access token")
        except (HTTPException, KeyError, TypeError, ValueError):
            raise StorageError("PDF storage sign-in failed") from None
        _token = credentials, token, monotonic() + max(0, ttl - 30)
        return token


def storage_request(method: str, path: str, **options) -> bytes:
    config = auth.settings()
    if config is None:
        raise StorageError("PDF storage is not configured")
    url, key = config
    token = storage_token()
    headers = {"apikey": key, "Authorization": f"Bearer {token}", **options.pop("headers", {})}
    try:
        with httpx.Client(timeout=httpx.Timeout(18, connect=5), follow_redirects=False) as client:
            response = client.request(method, f"{url}/storage/v1/{path}", headers=headers, **options)
            response.raise_for_status()
            return response.content
    except httpx.HTTPError:
        raise StorageError("PDF storage request failed") from None


def pdf_content(pdf: dict) -> bytes:
    key = pdf.get("object_key")
    if key is None:
        return pdf["content"]
    if not re.fullmatch(r"[0-9a-f]{32}\.pdf", key):
        raise StorageError("Invalid PDF reference")
    content = storage_request("GET", f"object/authenticated/{BUCKET}/{key}")
    if sha256(content).hexdigest() != pdf["content_sha256"]:
        raise StorageError("PDF storage checksum mismatch")
    return content


def matches(pdf: dict, content: bytes) -> bool:
    if pdf.get("object_key"):
        return pdf["content_sha256"] == sha256(content).hexdigest()
    return pdf["content"] == content


def stage(content: bytes) -> tuple[str, str]:
    key = f"{uuid4().hex}.pdf"
    digest = sha256(content).hexdigest()
    # Reserve cleanup before contacting Storage so a crashed upload leaves a durable cleanup ticket.
    with database.connect() as connection:
        connection.execute("INSERT INTO original_cleanup (object_key, not_before) VALUES (?, now() + interval '1 hour')", (key,))
    storage_request("POST", f"object/{BUCKET}/{key}", content=content,
                    headers={"Content-Type": "application/pdf", "x-upsert": "false", "cache-control": "no-store"})
    return key, digest


def publish(connection, key: str) -> None:
    reserved = connection.execute("""DELETE FROM original_cleanup WHERE object_key = ?
        AND not_before > clock_timestamp() RETURNING object_key""", (key,)).fetchone()
    if reserved is None:
        raise StorageError("PDF upload reservation expired")


def cleanup() -> int:
    if not postgres.database_url():
        return 0
    with database.connect() as connection:
        rows = connection.execute("""DELETE FROM original_cleanup WHERE object_key IN (
            SELECT q.object_key FROM original_cleanup q WHERE q.not_before <= now()
            AND NOT EXISTS (SELECT 1 FROM document_pdfs p WHERE p.object_key = q.object_key)
            ORDER BY q.not_before LIMIT 100
        ) RETURNING object_key""").fetchall()
        keys = [row["object_key"] for row in rows]
        if keys:
            storage_request("DELETE", f"object/{BUCKET}", json={"prefixes": keys})
    return len(keys)


def retry_cleanup() -> None:
    try:
        cleanup()
    except Exception as error:
        logger.warning("PDF storage cleanup postponed: %s", type(error).__name__)
