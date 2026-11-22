"""Document catalog, permissions, uploads, and stored originals."""

import json
import sqlite3

from .. import postgres
from ..database import connect
from ..domain import Document


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
    from .. import originals

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
            from ..vector_index import INSERT

            connection.executemany(INSERT, chunks)
            connection.execute("DELETE FROM rag_chunks WHERE document_id = ? AND profile = 0 AND index_key <> ?",
                               (record.id, index_key))
