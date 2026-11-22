"""Reproduce three trace-based repairs in a disposable SQLite/Chroma instance."""

import asyncio
import json
import logging
import os
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from ingest import ingest

from backend.app import database, rag, summarizer, telemetry, tool_execution
from backend.app.api import ai
from backend.app.main import create_app

QUESTION = "Можно ли сначала бесплатно попробовать занятие? Сколько оно длится?"
FACT = "Вводное занятие центра «Орбита» бесплатное и длится 30 минут."


def model(request):
    body = json.loads(request.content)
    payload = json.loads(body["input"])
    if body["text"]["format"]["name"] == "AnswerVerification":
        result = {"missing_parts": [], "unsupported_claims": []}
    else:
        fragment = next(row for row in payload["fragments"] if FACT in row["text"])
        result = {"answer": FACT, "answerable": True, "citations": [fragment["chunk_id"]],
                  "evidence": [{"question_part": QUESTION, "chunk_id": fragment["chunk_id"], "quote": FACT}]}
    return httpx.Response(200, json={"object": "response", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}]},
    ]})


def verify(output: Path) -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    scratch = Path("output/supabase-check")
    scratch.mkdir(parents=True, exist_ok=True)
    # Chroma's Windows file handles can stay open until this process exits.
    with TemporaryDirectory(dir=scratch, ignore_cleanup_errors=True) as folder, patch.dict(os.environ, {
        "KNOWLEDGE_DESK_DATABASE_URL": "", "KNOWLEDGE_DESK_SUPABASE_URL": "",
        "KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY": "", "KNOWLEDGE_DESK_REQUIRE_AUTH": "0",
        "KNOWLEDGE_DESK_CHUNK_SIZE": "600", "OPENAI_API_KEY": "diagnostic-fixture-only",
    }):
        folder = Path(folder)
        index = folder / "valid.chroma"
        indexed = ingest(index, chunk_size=600)
        assert indexed["status"] == "complete" and indexed["chunks"] > 0
        wrong = folder / "missing-collection"
        wrong.mkdir()
        cases = {}

        def sdk(**kwargs):
            return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))

        async def drop_context(question, _context):
            return await rag.answer_question(question, [])

        async def stall(_args):
            await asyncio.Event().wait()

        def record(response):
            trace = response.headers["X-Trace-ID"]
            records = [item for line in stream.getvalue().splitlines()
                       if (item := json.loads(line))["trace_id"] == trace]
            return {"trace_id": trace, "http_status": response.status_code, "stages": records,
                    "answerable": response.json().get("answerable"),
                    "draft_status": response.json().get("status"), "stop_code": response.json().get("stop_code")}

        telemetry.logger.addHandler(handler)
        previous_level = telemetry.logger.level
        telemetry.logger.setLevel(logging.INFO)
        try:
            with patch.object(database, "DB_PATH", folder / "data.sqlite3"), patch.object(rag, "INDEX", index), \
                    patch.object(summarizer, "AsyncOpenAI", sdk), TestClient(create_app()) as client:
                def ask():
                    return record(client.post("/ask", json={"question": QUESTION}))

                cases["baseline"] = ask()
                assert cases["baseline"]["answerable"] is True
                with patch.object(rag, "INDEX", wrong):
                    cases["wrong_collection"] = {"before": ask()}
                    assert client.get("/health").status_code == 503
                cases["wrong_collection"]["after"] = ask()
                with patch.object(ai, "answer_question", drop_context):
                    cases["lost_context"] = {"before": ask()}
                cases["lost_context"]["after"] = ask()

                def prepare():
                    draft = client.post("/consultations", json={"name": "Тестовая запись", "question": QUESTION}).json()
                    return record(client.post(f"/consultations/{draft['draft_id']}/prepare",
                                              json={"version": draft["version"]}))

                with patch.dict(tool_execution.TOOLS, {
                    "search_documents": replace(tool_execution.TOOLS["search_documents"], run=stall),
                }):
                    cases["tool_timeout"] = {"before": prepare()}
                cases["tool_timeout"]["after"] = prepare()
        finally:
            telemetry.logger.removeHandler(handler)
            telemetry.logger.setLevel(previous_level)

    wrong, lost, tool = (cases[name] for name in ("wrong_collection", "lost_context", "tool_timeout"))
    assert any(row["stage"] == "retrieval" and row["manifest_available"] is False for row in wrong["before"]["stages"])
    assert any(row["stage"] == "context" and row["selected"] > 0 for row in lost["before"]["stages"])
    assert any(row["stage"] == "grounding" and row["context_count"] == 0 for row in lost["before"]["stages"])
    assert wrong["after"]["answerable"] and lost["after"]["answerable"]
    assert tool["before"]["stop_code"] == "tool_timeout" and tool["after"]["draft_status"] == "awaiting_confirmation"
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    verify(Path("data/eval/deployment-diagnostics.json"))
