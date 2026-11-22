"""Copy a read-only SQLite snapshot to Knowledge Desk's Postgres schema."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from backend.app import database, postgres
from ingest import load_corpus, normalize

# Ordered by foreign-key dependency. Identifiers below are fixed application schema names.
KEYS = {
    "documents": ("id",),
    "document_pdfs": ("document_id",),
    "document_uploads": ("document_id",),
    "hidden_manifest_sources": ("collection", "source"),
    "requests": ("id",),
    "request_replies": ("id",),
    "consultation_drafts": ("draft_id",),
    "consultation_receipts": ("user_id", "operation_key"),
    "integration_events": ("event_id",),
}


def snapshot(source: Path, path: Path) -> dict[str, list[dict]]:
    original = sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    try:
        with closing(sqlite3.connect(path)) as copy:
            original.backup(copy)
    finally:
        original.close()
    old_path = database.DB_PATH
    url = os.environ.pop("KNOWLEDGE_DESK_DATABASE_URL", None)
    try:
        database.DB_PATH = path
        database.init_db()
        with database.connect() as connection:
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in (*KEYS, "document_access")}
    finally:
        database.DB_PATH = old_path
        if url is not None:
            os.environ["KNOWLEDGE_DESK_DATABASE_URL"] = url


def insert_or_verify(connection, table: str, keys: tuple[str, ...], values: dict) -> None:
    columns = ", ".join(values)
    parameters = ", ".join("?" for _ in values)
    key_columns = ", ".join(keys)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({parameters}) ON CONFLICT ({key_columns}) DO NOTHING",
                       tuple(values.values()))
    condition = " AND ".join(f"{key} = ?" for key in keys)
    row = connection.execute(f"SELECT {columns} FROM {table} WHERE {condition}", tuple(values[key] for key in keys)).fetchone()
    if row != values:
        raise ValueError(f"Conflicting destination row in {table} for {tuple(values[key] for key in keys)}")


def migrate(source: Path, owner: str) -> dict[str, int]:
    if not postgres.database_url():
        raise ValueError("Configure KNOWLEDGE_DESK_DATABASE_URL for the destination.")
    owner = str(UUID(owner))
    with TemporaryDirectory() as temp:
        data = snapshot(source, Path(temp) / "source.sqlite3")
    if any(row["status"] == "awaiting_confirmation" for row in data["consultation_drafts"]):
        raise ValueError("Finish or cancel awaiting consultations before migrating. Their approval is bound to local checkpoints.")
    if any(row.pop("kind", None) is not None for row in data["requests"]):
        raise ValueError("The legacy migration does not copy certificate files. Use a dedicated test database for the pilot.")
    manifest = {(entry.source, entry.title, entry.text) for entry in load_corpus().values()}
    access = {row["document_id"]: row for row in data["document_access"]}
    uploaded = {row["document_id"] for row in data["document_uploads"]}
    with database.connect() as connection:
        member = connection.execute("SELECT role FROM members WHERE user_id = ?", (owner,)).fetchone()
        if member is None or member["role"] != "editor":
            raise ValueError("The migration owner must be an existing editor.")
        for table, keys in KEYS.items():
            for row in data[table]:
                if row.get("user_id") == "local":
                    row["user_id"] = owner
                insert_or_verify(connection, table, keys, row)
        for row in data["documents"]:
            if value := access.get(row["id"]):
                value = {**value, "owner_id": owner if value["owner_id"] == "local" else value["owner_id"]}
            else:
                builtin = (row["source"], row["title"], normalize(row["text"])) in manifest and row["id"] not in uploaded
                value = {"document_id": row["id"], "owner_id": owner, "shared": int(builtin)}
            insert_or_verify(connection, "document_access", ("document_id",), value)
    return {table: len(data[table]) for table in KEYS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=database.DB_PATH)
    parser.add_argument("--owner", required=True, help="Supabase UUID of the legacy data owner")
    args = parser.parse_args()
    for table, count in migrate(args.source, args.owner).items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
