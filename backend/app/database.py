"""Open database transactions and validate the active persistence schema."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import postgres
from .storage import sqlite_schema

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


def check_postgres_schema(connection: postgres.Connection) -> None:
    connection.execute("""SELECT documents.id, members.role, members.can_operate_requests, members.certificate_issuer,
           member_profiles.avatar_version, rag_chunks.chunk_id, document_pdfs.object_key,
           request_reply_drafts.revision, request_replies.author_id, requests.kind
           FROM documents CROSS JOIN members CROSS JOIN member_profiles CROSS JOIN rag_chunks CROSS JOIN document_pdfs
           CROSS JOIN request_reply_drafts CROSS JOIN request_replies CROSS JOIN requests LIMIT 0""")
    if os.environ.get("KNOWLEDGE_DESK_TEST_CERTIFICATE_REGISTRY"):
        connection.execute("""SELECT request_certificates.request_id, certificate_notifications.provider_email_id,
               certificate_notifications.email_status, certificate_clarifications.id
               FROM request_certificates CROSS JOIN certificate_notifications
               CROSS JOIN certificate_clarifications LIMIT 0""")


def init_db() -> None:
    if postgres.database_url():
        with connect() as connection:
            check_postgres_schema(connection)
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect(immediate=True) as connection:
        sqlite_schema.initialize(connection)
