"""Own the version, decision, and atomic submission of a consultation draft."""

import hashlib
import json
from uuid import uuid4

from . import database, postgres
from .domain import NotFoundError
from .integration_delivery import queue_created
from .services import new_request


class DraftConflict(Exception):
    def __init__(self, code: str):
        self.code = code


def read_row(connection, user_id: str, draft_id: str, *, lock: bool = False) -> dict:
    row = connection.execute(
        "SELECT * FROM consultation_drafts WHERE user_id = ? AND draft_id = ?" + (postgres.lock_row() if lock else ""),
        (user_id, draft_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("Consultation draft not found.")
    return dict(row)


def get_draft(user_id: str, draft_id: str) -> dict:
    with database.connect() as connection:
        return read_row(connection, user_id, draft_id)


def create_draft(user_id: str, name: str, question: str) -> dict:
    draft_id = str(uuid4())
    with database.connect(immediate=True) as connection:
        connection.execute(
            "INSERT INTO consultation_drafts (draft_id, user_id, version, name, question, status) VALUES (?, ?, 1, ?, ?, 'draft')",
            (draft_id, user_id, name, question),
        )
        return read_row(connection, user_id, draft_id)


def check_version(row: dict, version: int) -> None:
    if row["version"] != version:
        raise DraftConflict("stale_version")


def revise_draft(user_id: str, draft_id: str, version: int, name: str, question: str) -> dict:
    with database.connect(immediate=True) as connection:
        row = read_row(connection, user_id, draft_id, lock=True)
        check_version(row, version)
        if row["status"] not in {"draft", "awaiting_confirmation", "failed"}:
            raise DraftConflict("draft_closed")
        connection.execute(
            "UPDATE consultation_drafts SET version = version + 1, name = ?, question = ?, status = 'draft', stop_code = NULL "
            "WHERE draft_id = ? AND user_id = ?", (name, question, draft_id, user_id),
        )
        return read_row(connection, user_id, draft_id)


def set_outcome(user_id: str, draft_id: str, version: int, status: str, code: str | None = None) -> dict:
    with database.connect(immediate=True) as connection:
        row = read_row(connection, user_id, draft_id, lock=True)
        check_version(row, version)
        if row["status"] in {"draft", "awaiting_confirmation"}:
            connection.execute(
                "UPDATE consultation_drafts SET status = ?, stop_code = ? WHERE user_id = ? AND draft_id = ?",
                (status, code, user_id, draft_id),
            )
            row = read_row(connection, user_id, draft_id)
        return row


def payload_hash(name: str, question: str) -> str:
    payload = json.dumps([name, question], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def operation_key(draft_id: str, version: int) -> str:
    return f"{draft_id}:{version}"


def check_confirmation(connection, user_id: str, draft_id: str, version: int, digest: str, *, lock: bool = False) -> dict:
    row = read_row(connection, user_id, draft_id, lock=lock)
    check_version(row, version)
    receipt = connection.execute(
        "SELECT payload_hash, request_id FROM consultation_receipts WHERE user_id = ? AND operation_key = ?",
        (user_id, operation_key(draft_id, version)),
    ).fetchone()
    if digest != payload_hash(row["name"], row["question"]) or (receipt and digest != receipt["payload_hash"]):
        raise DraftConflict("payload_conflict")
    if receipt:
        return {**row, "request_id": receipt["request_id"]}
    if row["status"] != "awaiting_confirmation":
        raise DraftConflict("not_awaiting_confirmation")
    return row


def confirmation_status(user_id: str, draft_id: str, version: int, digest: str) -> dict:
    with database.connect() as connection:
        return check_confirmation(connection, user_id, draft_id, version, digest)


def submit_confirmed(user_id: str, draft_id: str, version: int, digest: str) -> str:
    with database.connect(immediate=True) as connection:
        row = check_confirmation(connection, user_id, draft_id, version, digest, lock=True)
        if row["request_id"]:
            return row["request_id"]
        request = new_request(row["name"], row["question"], user_id=user_id)
        database.insert_request(connection, request)
        connection.execute(
            "INSERT INTO consultation_receipts (user_id, operation_key, payload_hash, request_id) VALUES (?, ?, ?, ?)",
            (user_id, operation_key(draft_id, version), digest, request.id),
        )
        connection.execute(
            "UPDATE consultation_drafts SET status = 'submitted', request_id = ? WHERE user_id = ? AND draft_id = ?",
            (request.id, user_id, draft_id),
        )
        queue_created(connection, user_id, request.id)
        return request.id
