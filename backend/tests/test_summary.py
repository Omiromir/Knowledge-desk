import asyncio
import json
import sqlite3
from contextlib import closing
from threading import BoundedSemaphore
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import AsyncOpenAI

from backend.app import database, summarizer

EXPECTED = {
    "summary": "Вопрос о модуле по тестированию.",
    "request_type": "course_question",
    "needs_human": False,
}


def provider_response(text: str, status="completed") -> httpx.Response:
    return httpx.Response(200, json={
        "object": "response", "status": status,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    })


@pytest.fixture
def provider(monkeypatch):
    handle = AsyncMock(return_value=provider_response(json.dumps(EXPECTED)))
    sdk = Mock(side_effect=lambda **settings: AsyncOpenAI(
        **settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ))
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setattr(summarizer, "AsyncOpenAI", sdk)
    monkeypatch.setattr(summarizer, "_attempts", BoundedSemaphore(summarizer.MAX_ATTEMPTS))
    return sdk, handle


def test_summary_is_validated_and_does_not_create_a_request(client, provider):
    sdk, handle = provider
    response = client.post("/ai/summary", json={"text": "  Есть ли модуль по тестированию?  "})

    assert response.status_code == 200
    assert response.json() == EXPECTED
    handle.assert_awaited_once()
    sent = json.loads(handle.call_args.args[0].content)
    assert sent["input"] == "Есть ли модуль по тестированию?"
    assert sent["text"]["format"]["strict"] is True
    assert sent["store"] is False
    assert sent["max_output_tokens"] == 256
    assert sdk.call_args.kwargs["timeout"] == 15
    assert sdk.call_args.kwargs["max_retries"] == 0
    with closing(sqlite3.connect(database.DB_PATH)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone() == (0,)


def test_custom_corpus_uses_general_request_types(client, provider, monkeypatch):
    monkeypatch.setattr(summarizer, "DEMO_CORPUS", False)
    expected = {**EXPECTED, "request_type": "information_request"}
    provider[1].return_value = provider_response(json.dumps(expected))

    response = client.post("/ai/summary", json={"text": "Где указан срок уведомления в договоре?"})

    assert response.status_code == 200
    assert response.json() == expected
    sent = json.loads(provider[1].call_args.args[0].content)
    request_types = sent["text"]["format"]["schema"]["properties"]["request_type"]["enum"]
    assert request_types == ["information_request", "consultation", "out_of_scope"]


@pytest.mark.parametrize("body", [{}, {"text": "  "}, {"text": "x" * 4001}, {"text": " " * 4000 + "x"}])
def test_invalid_input_does_not_call_provider(client, provider, body):
    response = client.post("/ai/summary", json=body)

    assert response.status_code == 422
    issue = response.json()["detail"][0]
    assert issue["loc"] == ["body", "text"]
    if len(body.get("text", "")) > 4000:
        assert issue["type"] == "string_too_long"
        assert issue["ctx"]["max_length"] == 4000
    provider[0].assert_not_called()
    provider[1].assert_not_awaited()


def test_input_at_limit_is_accepted(client, provider):
    response = client.post("/ai/summary", json={"text": "я" * 4000})
    assert response.status_code == 200
    assert response.json() == EXPECTED
    provider[1].assert_awaited_once()


@pytest.mark.parametrize(
    ("provider_reply", "status", "code"),
    [
        (httpx.Response(429, json={"error": {"message": "private detail"}}), 429, "provider_rate_limit"),
        (httpx.Response(500, json={"error": {"message": "private detail"}}), 502, "provider_unavailable"),
        (httpx.Response(200, content="{broken", headers={"Content-Type": "application/json"}), 502, "invalid_summary"),
        (httpx.Response(200, json={"status": "completed"}), 502, "invalid_summary"),
        (provider_response("{broken"), 502, "invalid_summary"),
        (provider_response(json.dumps({**EXPECTED, "needs_human": "false"})), 502, "invalid_summary"),
        (provider_response(json.dumps({**EXPECTED, "request_type": "unexpected"})), 502, "invalid_summary"),
        (provider_response(json.dumps(EXPECTED), "incomplete"), 502, "invalid_summary"),
        (httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "private detail"}]},
        ]}), 502, "provider_refused"),
    ],
)
def test_provider_failure_returns_safe_error_without_retry(client, provider, provider_reply, status, code):
    provider[1].return_value = provider_reply
    response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert response.json()["detail"]["message"]
    assert "private detail" not in response.text
    provider[1].assert_awaited_once()


def test_network_timeout_does_not_retry(client, provider):
    provider[1].side_effect = httpx.ReadTimeout("provider read timed out")
    response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "provider_timeout"
    provider[1].assert_awaited_once()


def test_total_timeout_cancels_waiting(client, provider, monkeypatch):
    deadline = asyncio.Timeout(None)
    timeout = Mock(return_value=deadline)
    monkeypatch.setattr(summarizer, "asyncio", SimpleNamespace(timeout=timeout))
    cancelled = False

    async def stalled(_request):
        nonlocal cancelled
        # Expire the deadline after the SDK reaches the transport.
        deadline.reschedule(asyncio.get_running_loop().time())
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled = True
            raise

    provider[1].side_effect = stalled
    response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})

    timeout.assert_called_once_with(15)
    assert deadline.expired()
    assert cancelled
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "provider_timeout"
    provider[1].assert_awaited_once()


def test_missing_key_keeps_service_optional(client, provider, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "not_configured"
    provider[0].assert_not_called()


def test_local_budget_blocks_extra_provider_attempts(client, provider):
    for _ in range(summarizer.MAX_ATTEMPTS):
        response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})
        assert response.status_code == 200
        assert response.json() == EXPECTED

    response = client.post("/ai/summary", json={"text": "Тестовый вопрос"})

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "local_limit"
    assert provider[1].await_count == summarizer.MAX_ATTEMPTS
