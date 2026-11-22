import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from backend.app import rag, rate_limit, tool_execution, uploads
from backend.app.api import ai, documents

from .conftest import MARKER, message, snapshot

SUMMARY = {"summary": "Учебный вопрос.", "request_type": "course_question", "needs_human": False}


@pytest.mark.parametrize(("path", "field", "length"), [
    ("/ask", "question", 1201),
    ("/ai/tools", "message", 1201),
    ("/ai/summary", "text", 4001),
])
def test_oversize_text_never_reaches_retrieval_or_model(security, monkeypatch, path, field, length):
    search = Mock()
    monkeypatch.setattr(ai, "retrieve_question", search)
    before = snapshot()
    reply = security.client.post(path, json={field: "x" * length}, headers=security.headers("A"))
    assert reply.status_code == 422 and reply.json()["detail"][0]["type"] == "string_too_long"
    search.assert_not_called()
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
    assert snapshot() == before


def test_oversize_document_stops_before_parsing_and_indexing(security, monkeypatch):
    monkeypatch.setattr(documents, "MAX_PLAIN_BYTES", 12)
    add = Mock()
    monkeypatch.setattr(uploads, "add", add)
    before = snapshot()
    reply = security.client.post("/documents/upload?filename=fixture.txt", content=b"x" * 13,
                                 headers=security.headers("A"))
    assert reply.status_code == 413 and reply.json()["detail"]["code"] == "upload_too_large"
    add.assert_not_called()
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
    assert snapshot() == before


def test_oversize_tool_json_is_rejected_before_parsing(security, monkeypatch):
    monkeypatch.setattr(tool_execution, "MAX_TOOL_ARGUMENT_BYTES", 16)
    validate = Mock()
    monkeypatch.setattr(tool_execution.SearchArguments, "model_validate_json", validate)
    context = tool_execution.ToolContext(security.users["A"], frozenset(tool_execution.TOOLS), "editor")
    before = snapshot()
    outcome = asyncio.run(tool_execution.execute_tool("search_documents", "а" * 9, context)).result
    assert outcome.status == "error" and outcome.code == "tool_arguments_too_large" and outcome.attempts == 0
    validate.assert_not_called()
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
    assert snapshot() == before


def test_oversize_tool_result_is_discarded(security, monkeypatch):
    name = "search_documents"
    run = AsyncMock(return_value={"fragments": [{"chunk_id": str(i), "title": "Тест", "text": "Фрагмент"} for i in range(6)]})
    monkeypatch.setitem(tool_execution.TOOLS, name, replace(tool_execution.TOOLS[name], run=run))
    context = tool_execution.ToolContext(security.users["A"], frozenset({name}), "editor")
    before = snapshot()
    outcome = asyncio.run(tool_execution.execute_tool(name, '{"query":"Архив","limit":5}', context)).result
    assert outcome.status == "error" and outcome.code == "invalid_tool_output" and outcome.attempts == 1
    run.assert_awaited_once()
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
    assert snapshot() == before


def test_rag_only_sends_top_five_even_if_retriever_returns_more(security, monkeypatch):
    rows = [
        {"chunk_id": f"chunk-{i}", "doc_id": "doc", "title": "Учебный документ", "text": f"Фрагмент {i}",
         "source": "test.md", "version_date": "2026-11-08", "distance": 0.1}
        for i in range(6)
    ]
    monkeypatch.setattr(ai, "retrieve_question", Mock(return_value=rows))
    result = AsyncMock(return_value=rag.AskResult(answer="Отказ теста", answerable=False, citations=[]))
    monkeypatch.setattr(rag, "generate_answer", result)
    reply = security.client.post("/ask", json={"question": "Вымышленный вопрос?"}, headers=security.headers("A"))
    assert reply.status_code == 200
    context = result.await_args.args[1]
    assert [citation.chunk_id for citation in context] == [row["chunk_id"] for row in rows[:5]]
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()


def test_minute_rate_limit_is_early_and_independent_for_each_user(security, monkeypatch):
    monkeypatch.setattr(rate_limit, "monotonic", lambda: 5)
    security.client.app.state.expensive_requests.limit = 1
    security.provider.side_effect = [message(SUMMARY), message(SUMMARY)]
    before = snapshot()
    data = {"text": "Тестовый вопрос"}
    client = security.client
    assert client.post("/ai/summary", json=data, headers=security.headers("A")).status_code == 200
    rejected = client.post("/ai/summary", json=data, headers=security.headers("A"))
    assert rejected.status_code == 429 and rejected.json()["detail"]["code"] == "user_rate_limit"
    assert rejected.headers["Retry-After"] == "55"
    security.provider.assert_awaited_once()
    assert client.post("/ai/summary", json=data, headers=security.headers("B")).status_code == 200
    assert security.provider.await_count == 2 and snapshot() == before
    security.outgoing.assert_not_awaited()


@pytest.mark.parametrize(("provider_code", "expected"), [
    ("insufficient_quota", "provider_quota_exhausted"),
    ("rate_limit_exceeded", "provider_rate_limit"),
])
@pytest.mark.parametrize(("route", "field"), [("/ai/summary", "text"), ("/ai/tools", "message")])
def test_provider_limits_are_safe_and_do_not_retry(security, provider_code, expected, route, field):
    security.provider.side_effect = [httpx.Response(429, json={"error": {"code": provider_code, "message": MARKER}})]
    before = snapshot()
    response = security.client.post(route, json={field: "Вымышленный вопрос"}, headers=security.headers("A"))
    assert response.status_code == 429 and response.json()["detail"]["code"] == expected
    assert MARKER not in response.text and MARKER not in security.log.text
    security.provider.assert_awaited_once()
    assert snapshot() == before
    security.outgoing.assert_not_awaited()
