"""Store and retrieve the shared vector index with live catalog permissions."""

import json
import os
from math import isfinite

from ingest import CHUNK_SIZES, DIMENSION, TARGET
from retrieve import TOP_K

from . import database

INSERT = """INSERT INTO rag_chunks (
    chunk_id, document_id, doc_id, profile, index_key, source, version_date,
    ordinal, start_offset, end_offset, page_number, text, embedding
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::extensions.vector)
ON CONFLICT (chunk_id) DO NOTHING"""

# Exclude unpublished documents and superseded revisions before ranking or reading a citation.
VISIBLE = """FROM rag_chunks c JOIN documents d ON d.id = c.document_id
JOIN document_access a ON a.document_id = d.id
LEFT JOIN document_uploads u ON u.document_id = d.id
WHERE (a.owner_id = ? OR a.shared = 1) AND d.source = c.source AND (
    (c.profile = 0 AND u.index_key = c.index_key) OR
    (c.profile > 0 AND u.document_id IS NULL)
)"""

FIELDS = "c.chunk_id, c.doc_id, d.title, c.text, c.source, c.version_date, c.page_number"


def vector_literal(vector) -> str:
    values = [float(value) for value in vector]
    if len(values) != DIMENSION or not all(map(isfinite, values)) or not any(values):
        raise ValueError("Invalid E5 vector")
    return json.dumps(values, separators=(",", ":"))


def chunk_row(chunk_id: str, document_id: str | None, profile: int, text: str, metadata: dict, vector) -> tuple:
    return (chunk_id, document_id, metadata["doc_id"], profile, metadata.get("index_key"), metadata["source"],
            metadata["version_date"], metadata["ordinal"], metadata["start"], metadata["end"],
            metadata.get("page_number"), text, vector_literal(vector))


def profile() -> int:
    size = int(os.environ.get("KNOWLEDGE_DESK_CHUNK_SIZE", TARGET))
    if size not in CHUNK_SIZES:
        raise ValueError("Unsupported chunk profile")
    return size


def search(vector, user_id: str) -> list[dict]:
    with database.connect() as connection:
        return connection.execute(
            f"""SELECT {FIELDS}, c.embedding OPERATOR(extensions.<=>) ?::extensions.vector AS distance
                {VISIBLE} AND c.profile IN (0, ?)
                ORDER BY distance, c.chunk_id LIMIT ?""",
            (vector_literal(vector), user_id, profile(), TOP_K),
        ).fetchall()


def source(chunk_id: str, user_id: str) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            f"""SELECT {FIELDS}, d.text AS document_text, c.start_offset AS start, c.end_offset AS end
                {VISIBLE} AND c.chunk_id = ?""", (user_id, chunk_id),
        ).fetchone()
    if row is None:
        raise LookupError(chunk_id)
    return row


def has_manifest_chunks(document_id: str) -> bool:
    with database.connect() as connection:
        return connection.execute(
            "SELECT 1 FROM rag_chunks WHERE document_id = ? AND profile > 0 LIMIT 1", (document_id,),
        ).fetchone() is not None
