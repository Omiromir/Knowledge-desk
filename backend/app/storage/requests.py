"""Requests, reply history, and operator drafts."""

import sqlite3

from .. import postgres
from ..config import LOCAL_USER_ID
from ..database import connect
from ..domain import (
    NotFoundError, RequestRecord, RequestReply, RequestReplyConflict, RequestReplyDraftConflict,
    RequestStatus, RequestStatusConflict,
)


def create_request(record: RequestRecord) -> RequestRecord:
    try:
        with connect() as connection:
            insert_request(connection, record)
    except sqlite3.IntegrityError as error:
        if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
            raise NotFoundError("Document not found.") from error
        raise
    return record


def insert_request(connection: sqlite3.Connection, record: RequestRecord) -> None:
    connection.execute(
        """INSERT INTO requests (id, name, question, status, document_id, created_at, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (record.id, record.name, record.question, record.status, record.document_id, record.created_at, record.user_id),
    )


def request_with_replies(connection: sqlite3.Connection, row: sqlite3.Row) -> RequestRecord:
    order = "sort_order" if postgres.database_url() else "rowid"
    replies = connection.execute(
        f"""SELECT id, text, created_at, edited_at, author_id FROM request_replies
           WHERE request_id = ? AND deleted = 0 ORDER BY {order}""",
        (row["id"],),
    ).fetchall()
    return RequestRecord(**row, replies=tuple(RequestReply(**reply) for reply in replies) or None)


REQUEST_SCOPE = "(CAST(? AS TEXT) IS NULL OR user_id = ?)"
REQUEST_FIELDS = "id, name, question, status, document_id, created_at, user_id, kind"


def get_request(request_id: str, *, user_id: str | None = LOCAL_USER_ID) -> RequestRecord | None:
    with connect() as connection:
        row = connection.execute(
            f"SELECT {REQUEST_FIELDS} FROM requests WHERE id = ? AND {REQUEST_SCOPE}", (request_id, user_id, user_id)
        ).fetchone()
        return request_with_replies(connection, row) if row is not None else None


def list_requests(user_id: str | None) -> list[RequestRecord]:
    with connect() as connection:
        scope = (user_id, user_id)
        records = connection.execute(
            f"""SELECT {REQUEST_FIELDS} FROM requests WHERE {REQUEST_SCOPE}
                ORDER BY created_at DESC NULLS LAST, id""",
            scope,
        ).fetchall()
        if not records:
            return []
        order = "sort_order" if postgres.database_url() else "rowid"
        replies = connection.execute(
            f"""SELECT request_id, id, text, created_at, edited_at, author_id FROM request_replies
                WHERE deleted = 0 AND request_id IN (SELECT id FROM requests WHERE {REQUEST_SCOPE})
                ORDER BY request_id, {order}""",
            scope,
        )
        history: dict[str, list[RequestReply]] = {}
        for row in replies:
            reply = dict(row)
            request_id = reply.pop("request_id")
            history.setdefault(request_id, []).append(RequestReply(**reply))
        return [RequestRecord(**row, replies=tuple(history.get(row["id"], ())) or None) for row in records]


def update_request_status(
    request_id: str, expected_status: RequestStatus, status: RequestStatus, *, user_id: str | None = LOCAL_USER_ID,
) -> RequestRecord | None:
    with connect() as connection:
        row = connection.execute(
            f"""UPDATE requests SET status = ? WHERE id = ? AND {REQUEST_SCOPE} AND status IN (?, ?)
               RETURNING {REQUEST_FIELDS}""",
            (status, request_id, user_id, user_id, expected_status, status),
        ).fetchone()
        if row is not None:
            return request_with_replies(connection, row)
        if connection.execute(f"SELECT 1 FROM requests WHERE id = ? AND {REQUEST_SCOPE}", (request_id, user_id, user_id)).fetchone():
            raise RequestStatusConflict("The request status has changed.")
        return None


def set_draft_actor(connection, operator_id: str):
    if postgres.database_url():
        connection.execute("SELECT set_config('knowledge_desk.operator_id', ?, true)", (operator_id,))


def _reply_draft(connection, operator_id: str, request_id: str):
    return connection.execute(
        "SELECT revision, text FROM request_reply_drafts WHERE operator_id = ? AND request_id = ?",
        (operator_id, request_id),
    ).fetchone()


def get_reply_draft(request_id: str, operator_id: str):
    with connect() as connection:
        set_draft_actor(connection, operator_id)
        if not connection.execute("SELECT 1 FROM requests WHERE id = ?", (request_id,)).fetchone():
            raise NotFoundError("Request not found.")
        row = _reply_draft(connection, operator_id, request_id)
        return dict(row) if row is not None else None


def save_reply_draft(request_id: str, operator_id: str, text: str, revision: str, expected: str | None):
    with connect(immediate=True) as connection:
        set_draft_actor(connection, operator_id)
        if not connection.execute("SELECT id FROM requests WHERE id = ?" + postgres.lock_row(), (request_id,)).fetchone():
            raise NotFoundError("Request not found.")
        current = _reply_draft(connection, operator_id, request_id)
        current_revision = current["revision"] if current is not None else None
        if current_revision == revision and current["text"] == text:
            return dict(current)
        if current_revision != expected or current_revision == revision:
            raise RequestReplyDraftConflict()
        if not text:
            connection.execute(
                "DELETE FROM request_reply_drafts WHERE operator_id = ? AND request_id = ?", (operator_id, request_id),
            )
            return None
        if connection.execute("SELECT 1 FROM request_replies WHERE id = ?", (revision,)).fetchone():
            raise RequestReplyDraftConflict()
        connection.execute(
            """INSERT INTO request_reply_drafts (operator_id, request_id, revision, text) VALUES (?, ?, ?, ?)
               ON CONFLICT (operator_id, request_id) DO UPDATE SET revision = excluded.revision, text = excluded.text""",
            (operator_id, request_id, revision, text),
        )
        return {"revision": revision, "text": text}


def _clear_sent_draft(connection, operator_id: str | None, request_id: str, reply: RequestReply):
    if not operator_id:
        return
    set_draft_actor(connection, operator_id)
    draft = _reply_draft(connection, operator_id, request_id)
    if draft is not None and draft["revision"] == reply.id and draft["text"].strip() == reply.text:
        connection.execute(
            "DELETE FROM request_reply_drafts WHERE operator_id = ? AND request_id = ? AND revision = ?",
            (operator_id, request_id, reply.id),
        )


def add_request_reply(
    request_id: str, reply: RequestReply, *, user_id: str | None = LOCAL_USER_ID, operator_id: str | None = None,
) -> RequestRecord | None:
    with connect(immediate=True) as connection:
        row = connection.execute(f"SELECT {REQUEST_FIELDS} FROM requests WHERE id = ? AND {REQUEST_SCOPE}" + postgres.lock_row(),
                                 (request_id, user_id, user_id)).fetchone()
        if row is None:
            return None
        prior = connection.execute(
            "SELECT request_id, text, deleted, author_id FROM request_replies WHERE id = ?", (reply.id,),
        ).fetchone()
        if prior is not None:
            if (not prior["deleted"] and prior["request_id"] == request_id and prior["text"] == reply.text
                    and prior["author_id"] in (None, operator_id)):
                _clear_sent_draft(connection, operator_id, request_id, reply)
                return request_with_replies(connection, row)
            raise RequestReplyConflict("This reply identifier is already in use.")
        if row["status"] == "resolved":
            raise RequestReplyConflict("Reopen the request before replying.")
        inserted = connection.execute(
            """INSERT INTO request_replies (id, request_id, text, created_at, author_id)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            (reply.id, request_id, reply.text, reply.created_at, operator_id),
        )
        if inserted.rowcount != 1:
            raise RequestReplyConflict("This reply identifier is already in use.")
        updated = connection.execute(
            f"UPDATE requests SET status = 'resolved' WHERE id = ? RETURNING {REQUEST_FIELDS}", (request_id,),
        ).fetchone()
        _clear_sent_draft(connection, operator_id, request_id, reply)
        return request_with_replies(connection, updated)


def change_request_reply(
    request_id: str, reply_id: str, text: str | None, edited_at: str, *, user_id: str | None = LOCAL_USER_ID,
) -> RequestRecord | None:
    with connect(immediate=True) as connection:
        row = connection.execute(f"SELECT {REQUEST_FIELDS} FROM requests WHERE id = ? AND {REQUEST_SCOPE}" + postgres.lock_row(),
                                 (request_id, user_id, user_id)).fetchone()
        if row is None:
            return None
        if text is None:
            # Reserve the identifier so a delayed retry cannot recreate a deleted reply.
            result = connection.execute(
                "UPDATE request_replies SET text = '', deleted = 1 WHERE request_id = ? AND id = ?",
                (request_id, reply_id),
            )
        else:
            result = connection.execute(
                """UPDATE request_replies SET edited_at = CASE WHEN text = ? THEN edited_at ELSE ? END, text = ?
                   WHERE request_id = ? AND id = ? AND deleted = 0""",
                (text, edited_at, text, request_id, reply_id),
            )
        if not result.rowcount:
            return None
        return request_with_replies(connection, row)
