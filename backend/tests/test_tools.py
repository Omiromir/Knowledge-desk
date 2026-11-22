import asyncio
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.responses import ResponseFunctionToolCall

from backend.app import database, tool_assistant, tool_execution


@pytest.fixture
def tool_context():
    return tool_execution.ToolContext("fixture-user", frozenset({"search_documents", "prepare_request"}))


def snapshot():
    with database.connect() as connection:
        return list(connection.iterdump())


@pytest.mark.parametrize(("name", "arguments", "code"), [
    ("search_documents", '{"limit":2}', "invalid_arguments"),
    ("search_documents", '{"query":"курс","limit":100}', "invalid_arguments"),
    ("prepare_request", '{"name":"Ира","question":"Вопрос","owner_id":"other"}', "invalid_arguments"),
    ("run_shell", '{}', "unknown_tool"),
    ("prepare_request", '{"name":"Ира","question":"  "}', "invalid_arguments"),
    ("search_documents", '{"query":"курс","limit":true}', "invalid_arguments"),
    ("search_documents", '{"query":"  ","limit":2}', "invalid_arguments"),
    ("search_documents", '{broken', "invalid_arguments"),
])
def test_rejects_before_execution(client, monkeypatch, tool_context, name, arguments, code):
    run = AsyncMock(side_effect=AssertionError("A rejected call reached a tool"))
    for tool_name, tool in tool_execution.TOOLS.items():
        monkeypatch.setitem(tool_execution.TOOLS, tool_name, replace(tool, run=run))
    before = snapshot()
    result = asyncio.run(tool_execution.execute_tool(name, arguments, tool_context)).result
    assert result.status == "error" and result.code == code
    assert not result.retryable and result.attempts == 0
    run.assert_not_awaited()
    assert snapshot() == before


def test_permission_comes_from_server_context(client, monkeypatch):
    name = "prepare_request"
    run = AsyncMock()
    monkeypatch.setitem(tool_execution.TOOLS, name, replace(tool_execution.TOOLS[name], run=run))
    context = tool_execution.ToolContext("reader", frozenset({"search_documents"}))
    assert [tool["name"] for tool in tool_execution.tool_definitions(context)] == ["search_documents"]
    before = snapshot()
    result = asyncio.run(tool_execution.execute_tool(name, '{"name":"admin","question":"Продлить доступ"}', context))
    assert result.result.status == "error" and result.result.code == "forbidden_tool"
    run.assert_not_awaited()
    assert snapshot() == before


def test_valid_tools_return_validated_data_without_writes(client, monkeypatch, tool_context):
    hits = [
        {"chunk_id": f"chunk-{i}", "doc_id": "doc", "title": "Учебный курс", "text": f"Отрывок {i}",
         "source": "doc.md", "version_date": "2026-11-08", "distance": 0.1}
        for i in range(3)
    ]
    search = Mock(return_value=hits)
    monkeypatch.setattr(tool_execution, "retrieve_question", search)
    before = snapshot()
    found = asyncio.run(tool_execution.execute_tool("search_documents", '{"query":" курс ","limit":2}', tool_context))
    draft = asyncio.run(tool_execution.execute_tool("prepare_request", '{"name":" Ира ","question":" Пример? "}', tool_context))

    search.assert_called_once_with("курс")
    assert found.result.model_dump() == {
        "status": "success", "data": {"fragments": [
            {key: hit[key] for key in ("chunk_id", "title", "text")} for hit in hits[:2]
        ]}, "attempts": 1,
    }
    assert draft.result.model_dump() == {"status": "success", "data": {"name": "Ира", "question": "Пример?"}, "attempts": 1}
    assert snapshot() == before


@pytest.mark.parametrize(("failure", "expected_code", "attempts", "retryable"), [
    ("hang", "tool_timeout", 2, True),
    ("exception", "tool_failed", 2, True),
    ("wrong_shape", "invalid_tool_output", 1, False),
])
def test_search_failures_are_safe_and_bounded(client, monkeypatch, tool_context, caplog, failure, expected_code, attempts, retryable):
    name = "search_documents"
    secret = "fixture-secret-never-log"
    cancelled = []

    async def run(_):
        if failure == "hang":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        if failure == "exception":
            raise RuntimeError(secret)
        return {"unexpected": secret}

    tool = AsyncMock(side_effect=run)
    monkeypatch.setitem(tool_execution.TOOLS, name, replace(tool_execution.TOOLS[name], run=tool))
    assert tool_execution.TOOL_TIMEOUT == 3
    monkeypatch.setattr(tool_execution, "TOOL_TIMEOUT", 0.015)
    caplog.set_level(logging.INFO, logger=tool_execution.logger.name)
    before = snapshot()
    arguments = json.dumps({"query": secret, "limit": 2})
    execution = asyncio.run(asyncio.wait_for(tool_execution.execute_tool(name, arguments, tool_context), timeout=1))
    assert execution.result.status == "error" and execution.result.code == expected_code
    assert execution.result.attempts == tool.await_count == attempts
    assert execution.result.retryable is retryable
    if failure == "hang":
        assert len(cancelled) == attempts
    assert secret not in execution.model_dump_json() and secret not in caplog.text
    assert caplog.text.count("name=search_documents") == attempts
    assert "duration_ms=" in caplog.text and expected_code in caplog.text
    assert snapshot() == before


def test_draft_has_no_automatic_retry(client, monkeypatch, tool_context):
    name = "prepare_request"
    run = AsyncMock(side_effect=RuntimeError("internal error"))
    monkeypatch.setitem(tool_execution.TOOLS, name, replace(tool_execution.TOOLS[name], run=run))
    before = snapshot()
    result = asyncio.run(tool_execution.execute_tool(name, '{"name":"Ира","question":"Вопрос"}', tool_context)).result
    assert result.status == "error" and result.code == "tool_failed" and result.attempts == 1
    run.assert_awaited_once()
    assert snapshot() == before


@pytest.mark.parametrize("failure", [None, "tool", "receipt"])
def test_model_receives_validated_result_and_ui_retains_it(client, monkeypatch, failure):
    name = "prepare_request"
    if failure == "tool":
        run = AsyncMock(side_effect=RuntimeError("fixture-private-error"))
        monkeypatch.setitem(tool_execution.TOOLS, name, replace(tool_execution.TOOLS[name], run=run))
    call = ResponseFunctionToolCall(type="function_call", name=name, call_id="demo-call", arguments='{"name":"Мира","question":"Когда курс?"}')
    proposed = SimpleNamespace(status="completed", output=[call])
    # The follow-up is only a receipt, even if a provider erroneously proposes a second call.
    follow_up = TimeoutError() if failure == "receipt" else proposed
    create = AsyncMock(side_effect=[proposed, follow_up])
    model = AsyncMock()
    model.__aenter__.return_value.responses.create = create
    monkeypatch.setattr(tool_assistant, "AsyncOpenAI", Mock(return_value=model))
    monkeypatch.setattr(tool_assistant, "_attempts", Mock())
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")
    before = snapshot()

    response = client.post("/ai/tools", json={"message": "Подготовь черновик. Мира, когда курс?"})

    assert response.status_code == 200
    execution = response.json()["execution"]
    assert execution["tool"] == name
    assert execution["result"]["status"] == ("error" if failure == "tool" else "success")
    assert (response.json()["message"] is not None) == (failure == "receipt")
    assert create.await_count == 2
    proposal_request, receipt_request = [call.kwargs for call in create.await_args_list]
    assert [tool["name"] for tool in proposal_request["tools"]] == ["search_documents", "prepare_request"]
    assert all(tool["strict"] and not tool["parameters"]["additionalProperties"] for tool in proposal_request["tools"])
    assert receipt_request["tool_choice"] == "none"
    returned = receipt_request["input"][-1]
    assert returned["type"] == "function_call_output" and returned["call_id"] == call.call_id
    assert json.loads(returned["output"]) == execution["result"]
    assert "fixture-private-error" not in returned["output"] and "mock-key" not in response.text
    assert snapshot() == before
