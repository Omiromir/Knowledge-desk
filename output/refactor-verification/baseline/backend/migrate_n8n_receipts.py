"""Import exported n8n receipts without replacing the first acknowledgement."""

import argparse
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pydantic import TypeAdapter

from backend.app.integration_delivery import Receipt


def migrate(path: Path, database_url: str) -> int:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    receipts = TypeAdapter(list[Receipt]).validate_python(
        [{"status": "accepted", **row} for row in data]
    )
    with psycopg.connect(database_url) as connection:
        for receipt in receipts:
            original = connection.execute(
                """SELECT event_id, request_id, execution_id, matches_request
                   FROM knowledge_desk.record_n8n_receipt(%s, %s, %s)""",
                (receipt.event_id, receipt.request_id, receipt.execution_id),
            ).fetchone()
            if original != (receipt.event_id, receipt.request_id, receipt.execution_id, True):
                raise ValueError(f"Existing receipt differs for event {receipt.event_id}")
    return len(receipts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="JSON array of legacy receipt rows")
    args = parser.parse_args()
    load_dotenv("backend/.env.n8n")
    database_url = os.getenv("KNOWLEDGE_DESK_N8N_DATABASE_URL")
    if not database_url:
        parser.error("Set KNOWLEDGE_DESK_N8N_DATABASE_URL")
    print(f"Verified {migrate(args.export, database_url)} receipt(s).")
