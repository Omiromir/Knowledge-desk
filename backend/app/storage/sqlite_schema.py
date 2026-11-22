"""Create and upgrade the local SQLite schema within one transaction."""

import sqlite3


def initialize(connection: sqlite3.Connection) -> None:
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
    if "kind" not in request_columns:
        connection.execute("ALTER TABLE requests ADD COLUMN kind TEXT CHECK (kind = 'employment_certificate')")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS request_certificates (
            request_id TEXT PRIMARY KEY REFERENCES requests(id) ON DELETE CASCADE,
            draft BLOB NOT NULL,
            signed_pdf BLOB,
            issued_by TEXT,
            issued_at TEXT,
            CHECK ((issued_at IS NULL AND issued_by IS NULL AND signed_pdf IS NULL)
                OR (issued_at IS NOT NULL AND issued_by IS NOT NULL AND signed_pdf IS NOT NULL))
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS certificate_clarifications (
            request_id TEXT NOT NULL REFERENCES request_certificates(request_id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 4000),
            asked_by TEXT NOT NULL,
            asked_at TEXT NOT NULL,
            answer TEXT CHECK (length(answer) BETWEEN 1 AND 4000),
            answered_at TEXT,
            CHECK ((answer IS NULL) = (answered_at IS NULL)),
            PRIMARY KEY (request_id, id)
        )"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS certificate_clarifications_open
           ON certificate_clarifications (request_id) WHERE answered_at IS NULL"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS certificate_notifications (
            request_id TEXT PRIMARY KEY REFERENCES request_certificates(request_id) ON DELETE CASCADE,
            event_id TEXT NOT NULL UNIQUE,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('waiting', 'pending', 'unknown', 'delivered', 'failed'))
        )"""
    )
    notification_columns = {row["name"] for row in connection.execute("PRAGMA table_info(certificate_notifications)")}
    for column in ("provider_email_id", "email_status", "email_status_at"):
        if column not in notification_columns:
            connection.execute(f"ALTER TABLE certificate_notifications ADD COLUMN {column} TEXT")
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS certificate_notifications_email_id
           ON certificate_notifications (provider_email_id) WHERE provider_email_id IS NOT NULL"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS request_replies (
            id TEXT PRIMARY KEY NOT NULL,
            request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            edited_at TEXT,
            deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
            author_id TEXT REFERENCES members(user_id) ON DELETE SET NULL
        )"""
    )
    reply_columns = {row["name"] for row in connection.execute("PRAGMA table_info(request_replies)")}
    if "edited_at" not in reply_columns:
        connection.execute("ALTER TABLE request_replies ADD COLUMN edited_at TEXT")
    if "deleted" not in reply_columns:
        connection.execute("ALTER TABLE request_replies ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))")
    if "author_id" not in reply_columns:
        connection.execute("ALTER TABLE request_replies ADD COLUMN author_id TEXT REFERENCES members(user_id) ON DELETE SET NULL")
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
        user_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('reader', 'editor', 'operator', 'admin'))
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS member_profiles (
        user_id TEXT PRIMARY KEY REFERENCES members(user_id) ON DELETE CASCADE,
        display_name TEXT NOT NULL DEFAULT '' CHECK (length(display_name) <= 60),
        avatar BLOB CHECK (length(avatar) <= 131072),
        avatar_version TEXT,
        CHECK ((avatar IS NULL) = (avatar_version IS NULL))
    )""")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS members_one_admin ON members (role) WHERE role = 'admin'")
    member_columns = {row["name"] for row in connection.execute("PRAGMA table_info(members)")}
    for column, definition in (
        ("can_operate_requests", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("certificate_issuer", "BOOLEAN"),
    ):
        if column not in member_columns:
            connection.execute(f"ALTER TABLE members ADD COLUMN {column} {definition}")
    connection.execute("""CREATE TABLE IF NOT EXISTS request_reply_drafts (
        operator_id TEXT NOT NULL REFERENCES members(user_id) ON DELETE CASCADE,
        request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
        revision TEXT NOT NULL,
        text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 10000),
        PRIMARY KEY (operator_id, request_id)
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
