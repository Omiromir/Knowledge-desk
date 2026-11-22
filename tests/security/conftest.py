import json
import logging
from dataclasses import dataclass
from threading import BoundedSemaphore
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from backend.app import auth, database, integration_delivery, rag, summarizer, telemetry, tool_assistant, uploads
from backend.app.main import create_app

MARKER = "fixture-only-DO-NOT-OUTPUT-4b2e"


@dataclass
class SecurityCase:
    client: TestClient
    users: dict[str, str]
    provider: AsyncMock
    outgoing: AsyncMock
    log: pytest.LogCaptureFixture

    def headers(self, user: str) -> dict:
        return {"Authorization": f"Bearer {user}"} if user else {}

    def stages(self, response: httpx.Response) -> list[dict]:
        trace = response.headers["X-Trace-ID"]
        records = [json.loads(item.message) for item in self.log.records if item.name == telemetry.logger.name]
        return [item for item in records if item["trace_id"] == trace]


def snapshot() -> tuple[str, ...]:
    with database.connect() as connection:
        return tuple(connection.iterdump())


def provider_reply(*output: dict) -> httpx.Response:
    return httpx.Response(200, json={"object": "response", "status": "completed", "output": list(output)})


def message(body: dict) -> httpx.Response:
    return provider_reply({"type": "message", "content": [{"type": "output_text", "text": json.dumps(body)}]})


def proposal(name: str, arguments: dict) -> httpx.Response:
    return provider_reply({"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                           "name": name, "arguments": json.dumps(arguments), "status": "completed"})


@pytest.fixture
def security(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "security.sqlite3")
    monkeypatch.setenv("KNOWLEDGE_DESK_DATABASE_URL", "")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "mock-publishable")
    monkeypatch.setenv("KNOWLEDGE_DESK_EXPENSIVE_REQUESTS_PER_MINUTE", "30")
    monkeypatch.setenv("OPENAI_API_KEY", MARKER)
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", "http://127.0.0.1:5678/webhook-test/knowledge-desk-request-created-v2")
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_SECRET", "test-placeholder-no-external-requests")
    users = {label: str(uuid4()) for label in ("A", "B")}
    monkeypatch.setattr(auth, "provider", lambda _, *, token: {"id": users[token], "email": f"{token}@example.test"})
    monkeypatch.setattr(rag, "manifest_collection", lambda: None)
    vector = [1.0] + [0.0] * 383

    def embed(texts, _prefix):
        return [vector] * len(texts)

    monkeypatch.setattr(uploads, "embed", embed)
    monkeypatch.setattr(rag, "embed", embed)
    provider = AsyncMock(side_effect=AssertionError("Unexpected provider call"))

    def sdk(**settings):
        assert settings["max_retries"] == 0
        return AsyncOpenAI(**settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)))

    for module in (rag, summarizer, tool_assistant):
        monkeypatch.setattr(module, "_attempts", BoundedSemaphore(20))
    monkeypatch.setattr(summarizer, "AsyncOpenAI", sdk)
    monkeypatch.setattr(tool_assistant, "AsyncOpenAI", sdk)

    async def receiver(_url, _secret, event):
        return httpx.Response(200, json={"status": "accepted", "event_id": event["event_id"],
                                         "request_id": event["request_id"], "execution_id": "1"})

    outgoing = AsyncMock(side_effect=receiver)
    monkeypatch.setattr(integration_delivery, "post_event", outgoing)
    caplog.set_level(logging.INFO, logger=telemetry.logger.name)
    with TestClient(create_app()) as client:
        with database.connect() as connection:
            connection.executemany("INSERT INTO members (user_id, role) VALUES (?, 'editor')",
                                   [(identifier,) for identifier in users.values()])
        yield SecurityCase(client, users, provider, outgoing, caplog)
