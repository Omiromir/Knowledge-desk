import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import postgres
from .config import LOCAL_USER_ID
from .domain import (
    Document, NotFoundError, RequestRecord, RequestReply, RequestReplyConflict, RequestStatus, RequestStatusConflict,
)

DB_PATH = Path(
    os.environ.get(
        "KNOWLEDGE_DESK_DB_PATH",
        Path(__file__).resolve().parents[2] / "data" / "knowledge_desk.sqlite3",
    )
)


@contextmanager
def connect(*, immediate: bool = False):
    if url := postgres.database_url():
        with postgres.pool(url).connection() as connection:
            connection.execute("SET LOCAL search_path TO knowledge_desk, pg_catalog")
            yield postgres.Connection(connection)
        return
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    if postgres.database_url():
        with connect() as connection:
            connection.execute("SELECT role FROM members LIMIT 0")
            connection.execute("SELECT chunk_id FROM rag_chunks LIMIT 0")
            connection.execute("SELECT object_key FROM document_pdfs LIMIT 0")
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect(immediate=True) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS document_pdfs (
                document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                content BLOB NOT NULL,
                text_hash TEXT NOT NULL,
                page_spans TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS document_uploads (
                document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                version_date TEXT NOT NULL,
                index_key TEXT NOT NULL UNIQUE
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS hidden_manifest_sources (
                collection TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (collection, source)
            )"""
        )
        migrate_request_statuses(connection)
        request_columns = {row["name"] for row in connection.execute("PRAGMA table_info(requests)")}
        if "created_at" not in request_columns:
            connection.execute("ALTER TABLE requests ADD COLUMN created_at TEXT")
        if "user_id" not in request_columns:
            connection.execute("ALTER TABLE requests ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS request_replies (
                id TEXT PRIMARY KEY NOT NULL,
                request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                edited_at TEXT,
                deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))
            )"""
        )
        reply_columns = {row["name"] for row in connection.execute("PRAGMA table_info(request_replies)")}
        if "edited_at" not in reply_columns:
            connection.execute("ALTER TABLE request_replies ADD COLUMN edited_at TEXT")
        if "deleted" not in reply_columns:
            connection.execute("ALTER TABLE request_replies ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))")
        connection.execute("CREATE INDEX IF NOT EXISTS request_replies_request ON request_replies (request_id)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS consultation_drafts (
                draft_id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                name TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('draft', 'awaiting_confirmation', 'submitted', 'failed', 'cancelled')),
                stop_code TEXT,
                request_id TEXT REFERENCES requests(id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS consultation_receipts (
                user_id TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                request_id TEXT NOT NULL REFERENCES requests(id),
                PRIMARY KEY (user_id, operation_key)
            )"""
        )
        if "user_id" not in request_columns:
            connection.execute(
                """UPDATE requests SET user_id = (
                    SELECT consultation_drafts.user_id FROM consultation_drafts WHERE request_id = requests.id
                ) WHERE EXISTS (SELECT 1 FROM consultation_drafts WHERE request_id = requests.id)"""
            )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS integration_events (
                event_id TEXT PRIMARY KEY NOT NULL,
                request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
                user_id TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type = 'request.created'),
                status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','unknown')),
                reason TEXT,
                receipt TEXT,
                updated_at TEXT NOT NULL,
                receiver_key TEXT,
                attempt_until REAL
            )"""
        )
        event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(integration_events)")}
        if "receiver_key" not in event_columns:
            connection.execute("ALTER TABLE integration_events ADD COLUMN receiver_key TEXT")
        if "attempt_until" not in event_columns:
            connection.execute("ALTER TABLE integration_events ADD COLUMN attempt_until REAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS members (
            user_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('reader', 'editor', 'operator'))
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS document_access (
            document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL REFERENCES members(user_id), shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1))
        )""")


def migrate_request_statuses(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(requests)")}
    if columns:
        connection.execute("ALTER TABLE requests RENAME TO requests_legacy")
    connection.execute(
        """CREATE TABLE requests (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'in_progress', 'resolved')),
            document_id TEXT REFERENCES documents(id) ON DELETE SET NULL
        )"""
    )
    if columns:
        document_column = "document_id" if "document_id" in columns else "NULL"
        connection.execute(
            "INSERT INTO requests (id, name, question, status, document_id) "
            f"SELECT id, name, question, status, {document_column} FROM requests_legacy ORDER BY rowid"
        )
        connection.execute("DROP TABLE requests_legacy")
    connection.execute("PRAGMA user_version = 1")


def member_role(user_id: str) -> str | None:
    with connect() as connection:
        row = connection.execute("SELECT role FROM members WHERE user_id = ?", (user_id,)).fetchone()
    return row["role"] if row else None


def readable_documents(user_id: str) -> list[Document]:
    with connect() as connection:
        order = "d.sort_order" if postgres.database_url() else "d.rowid"
        rows = connection.execute(
            f"""SELECT d.id, d.title, d.text, d.source FROM documents d JOIN document_access a ON d.id = a.document_id
                WHERE a.owner_id = ? OR a.shared = 1 ORDER BY {order}""", (user_id,),
        ).fetchall()
    return [Document(**row) for row in rows]


def document_access(document_id: str, user_id: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM document_access WHERE document_id = ? AND (owner_id = ? OR shared = 1)",
            (document_id, user_id),
        ).fetchone()
    return row is not None


def sharing(document_id: str, user_id: str, shared: bool | None = None) -> dict | None:
    with connect() as connection:
        if shared is not None:
            connection.execute("UPDATE document_access SET shared = ? WHERE document_id = ? AND owner_id = ?",
                               (int(shared), document_id, user_id))
        row = connection.execute("SELECT shared FROM document_access WHERE document_id = ? AND owner_id = ?",
                                 (document_id, user_id)).fetchone()
    return {"shared": bool(row["shared"])} if row is not None else None


def list_documents() -> list[Document]:
    with connect() as connection:
        order = "sort_order" if postgres.database_url() else "rowid"
        rows = connection.execute(f"SELECT id, title, text, source FROM documents ORDER BY {order}").fetchall()
        return [Document(**row) for row in rows]


def create_document(record: Document, *, owner_id: str | None = None) -> Document:
    with connect() as connection:
        connection.execute(
            "INSERT INTO documents (id, title, text, source) VALUES (?, ?, ?, ?)",
            (record.id, record.title, record.text, record.source),
        )
        if owner_id is not None:
            connection.execute("INSERT INTO document_access (document_id, owner_id) VALUES (?, ?)", (record.id, owner_id))
    return record


def get_document(document_id: str) -> Document | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, title, text, source FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return Document(**row) if row is not None else None


def update_document(document_id: str, changes: dict[str, str]) -> Document | None:
    with connect() as connection:
        connection.execute(
            """UPDATE documents SET title = COALESCE(?, title),
                text = COALESCE(?, text), source = COALESCE(?, source) WHERE id = ?""",
            (changes.get("title"), changes.get("text"), changes.get("source"), document_id),
        )
        row = connection.execute(
            "SELECT id, title, text, source FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return Document(**row) if row is not None else None


def hide_manifest_source(connection, source: tuple[str, str] | None) -> None:
    if source is not None:
        connection.execute(
            "INSERT INTO hidden_manifest_sources (collection, source) VALUES (?, ?) ON CONFLICT DO NOTHING", source,
        )


def delete_document(document_id: str, *, hidden_source: tuple[str, str] | None = None) -> bool:
    with connect() as connection:
        deleted = connection.execute(
            "DELETE FROM documents WHERE id = ?", (document_id,)
        ).rowcount > 0
        if deleted:
            hide_manifest_source(connection, hidden_source)
        return deleted


def index_visibility(collection: str) -> tuple[list[sqlite3.Row], set[str]]:
    with connect() as connection:
        uploads = connection.execute(
            """SELECT d.*, u.version_date, u.index_key FROM document_uploads u
               JOIN documents d ON d.id = u.document_id"""
        ).fetchall()
        hidden = connection.execute(
            "SELECT source FROM hidden_manifest_sources WHERE collection = ?", (collection,),
        ).fetchall()
        return uploads, {row["source"] for row in hidden}


def uploaded_version(document_id: str) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute(
            "SELECT version_date, index_key FROM document_uploads WHERE document_id = ?",
            (document_id,),
        ).fetchone()


def get_pdf(document_id: str) -> dict | None:
    with connect() as connection:
        columns = "filename, content, object_key, content_sha256" if postgres.database_url() else "filename, content"
        row = connection.execute(
            f"SELECT {columns} FROM document_pdfs WHERE document_id = ?", (document_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def pdf_spans(document_id: str, text_hash: str) -> list[tuple[int, int, int]]:
    with connect() as connection:
        row = connection.execute(
            "SELECT page_spans FROM document_pdfs WHERE document_id = ? AND text_hash = ?",
            (document_id, text_hash),
        ).fetchone()
    if not row:
        return []
    return [(number, start, end) for number, start, end in json.loads(row["page_spans"])]


def save_uploaded_document(
    record: Document, version_date: str, index_key: str, *,
    update: bool, hidden_source: tuple[str, str] | None = None,
    pdf: tuple[str, bytes, str, str] | None = None,
    owner_id: str | None = None,
    chunks: list[tuple] | None = None,
) -> None:
    from . import originals

    stored_pdf = originals.stage(pdf[1]) if pdf is not None and postgres.database_url() else None
    with connect() as connection:
        if stored_pdf is not None:
            originals.publish(connection, stored_pdf[0])
        if update:
            connection.execute(
                "UPDATE documents SET title = ?, text = ?, source = ? WHERE id = ?",
                (record.title, record.text, record.source, record.id),
            )
        else:
            connection.execute(
                "INSERT INTO documents (id, title, text, source) VALUES (?, ?, ?, ?)",
                (record.id, record.title, record.text, record.source),
            )
            if owner_id is not None:
                connection.execute("INSERT INTO document_access (document_id, owner_id) VALUES (?, ?)", (record.id, owner_id))
        connection.execute(
            """INSERT INTO document_uploads (document_id, version_date, index_key) VALUES (?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET version_date = excluded.version_date,
                   index_key = excluded.index_key""",
            (record.id, version_date, index_key),
        )
        if stored_pdf is not None:
            filename, _, text_hash, page_spans = pdf
            connection.execute(
                """INSERT INTO document_pdfs (document_id, filename, text_hash, page_spans, object_key, content_sha256)
                   VALUES (?, ?, ?, ?, ?, ?)""", (record.id, filename, text_hash, page_spans, *stored_pdf),
            )
        elif pdf is not None:
            connection.execute(
                """INSERT INTO document_pdfs (document_id, filename, content, text_hash, page_spans)
                   VALUES (?, ?, ?, ?, ?)""",
                (record.id, *pdf),
            )
        hide_manifest_source(connection, hidden_source)
        if chunks is not None:
            from .vector_index import INSERT

            connection.executemany(INSERT, chunks)
            connection.execute("DELETE FROM rag_chunks WHERE document_id = ? AND profile = 0 AND index_key <> ?",
                               (record.id, index_key))


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
        f"""SELECT id, text, created_at, edited_at FROM request_replies
           WHERE request_id = ? AND deleted = 0 ORDER BY {order}""",
        (row["id"],),
    ).fetchall()
    return RequestRecord(**row, replies=tuple(RequestReply(**reply) for reply in replies) or None)


REQUEST_SCOPE = "(CAST(? AS TEXT) IS NULL OR user_id = ?)"


def get_request(request_id: str, *, user_id: str | None = LOCAL_USER_ID) -> RequestRecord | None:
    with connect() as connection:
        row = connection.execute(
            f"SELECT * FROM requests WHERE id = ? AND {REQUEST_SCOPE}", (request_id, user_id, user_id)
        ).fetchone()
        return request_with_replies(connection, row) if row is not None else None


def list_requests(user_id: str | None) -> list[RequestRecord]:
    with connect() as connection:
        rows = connection.execute(f"SELECT * FROM requests WHERE {REQUEST_SCOPE} ORDER BY created_at DESC NULLS LAST, id",
                                  (user_id, user_id)).fetchall()
        return [request_with_replies(connection, row) for row in rows]


def update_request_status(
    request_id: str, expected_status: RequestStatus, status: RequestStatus, *, user_id: str = LOCAL_USER_ID,
) -> RequestRecord | None:
    with connect() as connection:
        row = connection.execute(
            f"""UPDATE requests SET status = ? WHERE id = ? AND {REQUEST_SCOPE} AND status IN (?, ?)
               RETURNING *""",
            (status, request_id, user_id, user_id, expected_status, status),
        ).fetchone()
        if row is not None:
            return request_with_replies(connection, row)
        if connection.execute(f"SELECT 1 FROM requests WHERE id = ? AND {REQUEST_SCOPE}", (request_id, user_id, user_id)).fetchone():
            raise RequestStatusConflict("The request status has changed.")
        return None


def add_request_reply(request_id: str, reply: RequestReply, *, user_id: str = LOCAL_USER_ID) -> RequestRecord | None:
    with connect(immediate=True) as connection:
        row = connection.execute(f"SELECT * FROM requests WHERE id = ? AND {REQUEST_SCOPE}" + postgres.lock_row(),
                                 (request_id, user_id, user_id)).fetchone()
        if row is None:
            return None
        prior = connection.execute(
            "SELECT request_id, text, deleted FROM request_replies WHERE id = ?", (reply.id,),
        ).fetchone()
        if prior is not None:
            if not prior["deleted"] and prior["request_id"] == request_id and prior["text"] == reply.text:
                return request_with_replies(connection, row)
            raise RequestReplyConflict("This reply identifier is already in use.")
        if row["status"] == "resolved":
            raise RequestReplyConflict("Reopen the request before replying.")
        inserted = connection.execute(
            "INSERT INTO request_replies (id, request_id, text, created_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (reply.id, request_id, reply.text, reply.created_at),
        )
        if inserted.rowcount != 1:
            raise RequestReplyConflict("This reply identifier is already in use.")
        updated = connection.execute(
            "UPDATE requests SET status = 'resolved' WHERE id = ? RETURNING *", (request_id,),
        ).fetchone()
        return request_with_replies(connection, updated)


def change_request_reply(
    request_id: str, reply_id: str, text: str | None, edited_at: str, *, user_id: str = LOCAL_USER_ID,
) -> RequestRecord | None:
    with connect(immediate=True) as connection:
        row = connection.execute(f"SELECT * FROM requests WHERE id = ? AND {REQUEST_SCOPE}" + postgres.lock_row(),
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
