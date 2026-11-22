import asyncio
import json
import sqlite3
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from backend.app import database, tool_execution

from .conftest import MARKER, snapshot

PRIVATE = "private-fixture-A"
PAYLOAD = {"name": "Тест А", "question": PRIVATE}


@pytest.fixture
def objects(security):
    def create(user, path, payload):
        response = security.client.post(path, json=payload, headers=security.headers(user))
        assert response.status_code == 201, response.text
        return response.json()

    doc = create("A", "/documents", {"title": "Учебный файл", "text": PRIVATE, "source": "fixture-a.txt"})
    for path, payload in [
        ("/documents", {"title": "Собственный файл", "text": "B", "source": "fixture-b.txt"}),
        ("/requests", {"name": "Тест Б", "question": "Своя заявка"}),
        ("/consultations", {"name": "Тест Б", "question": "Свой черновик"}),
    ]:
        create("B", path, payload)
    headers = security.headers("A")

    def prepared_draft():
        draft = create("A", "/consultations", PAYLOAD)
        path = "/consultations/" + draft["draft_id"]
        prepare = security.client.post(path + "/prepare", json={"version": draft["version"]}, headers=headers)
        assert prepare.json()["status"] == "awaiting_confirmation"
        return path, {**PAYLOAD, "version": draft["version"]}

    path, submission = prepared_draft()
    response = security.client.post(path + "/confirm", json=submission, headers=headers)
    assert response.json()["delivery"]["status"] == "delivered"
    awaiting, version = prepared_draft()
    return "/documents/" + doc["id"], "/requests/" + response.json()["request_id"], awaiting, version


@pytest.mark.parametrize("user", ["B", ""], ids=["foreign-user", "anonymous"])
def test_object_mutations_and_receipts_are_denied_before_writes(security, objects, user, monkeypatch):
    doc, request, draft, submission = objects
    client = security.client
    headers = security.headers(user)
    writes = []
    connect = sqlite3.connect

    def watch_sql(sql):
        words = sql.lstrip().split(None, 1)
        if words and words[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(words[0])

    def traced_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(watch_sql)
        return connection

    monkeypatch.setattr(database.sqlite3, "connect", traced_connection)
    before = snapshot()
    for method, path, payload, expected in [
        ("GET", doc, None, 404),
        ("PATCH", doc, {"text": "Чужое изменение"}, 404),
        ("DELETE", doc, None, 404),
        ("GET", request, None, 404),
        ("PATCH", request, {"status": "in_progress", "expected_status": "new"}, 403),
        ("GET", draft, None, 404),
        ("PATCH", draft, submission, 404),
        ("POST", draft + "/confirm", submission, 404),
        ("GET", request + "/delivery", None, 404),
        ("POST", request + "/delivery/retry", None, 404),
        ("POST", "/requests", {**PAYLOAD, "document_id": doc.rsplit("/", 1)[-1]}, 404),
    ]:
        response = client.request(method, path, headers=headers, json=payload)
        assert response.status_code == (expected if user else 401), (method, path, response.text)
        assert PRIVATE not in response.text and MARKER not in response.text
        assert not any(row["stage"] in {"tool", "delivery", "model"} for row in security.stages(response))
    assert writes == [] and snapshot() == before
    security.outgoing.assert_awaited_once()
    security.provider.assert_not_awaited()


def test_owner_can_edit_read_and_confirm_with_one_delivery_per_request(security, objects):
    doc, request, draft, submission = objects
    client = security.client
    headers = security.headers("A")
    assert client.patch(doc, json={"text": "Своя правка"}, headers=headers).json()["text"] == "Своя правка"
    assert client.get(request, headers=headers).json()["question"] == PRIVATE
    assert client.get(request + "/delivery", headers=headers).json()["status"] == "delivered"
    first = client.post(draft + "/confirm", json=submission, headers=headers)
    repeated = client.post(draft + "/confirm", json=submission, headers=headers)
    assert first.json()["delivery"]["receipt"] == repeated.json()["delivery"]["receipt"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM integration_events").fetchone()[0] == 2
    assert security.outgoing.await_count == 2


@pytest.mark.parametrize(("name", "data", "code"), [
    pytest.param("submit_request", PAYLOAD, "unknown_tool", id="unknown-name"),
    pytest.param("prepare_request", {**PAYLOAD, "owner_id": "B"}, "invalid_arguments", id="owner-id"),
    pytest.param("search_documents", {"query": "Запрос", "limit": "2"}, "invalid_arguments", id="wrong-type"),
    pytest.param("search_documents", {"query": "Запрос", "limit": 6}, "invalid_arguments", id="fragment-limit"),
])
def test_direct_tool_rejection(security, monkeypatch, name, data, code):
    run = AsyncMock(return_value=PAYLOAD)
    for tool_name, tool in tool_execution.TOOLS.items():
        monkeypatch.setitem(tool_execution.TOOLS, tool_name, replace(tool, run=run))
    context = tool_execution.ToolContext(security.users["A"], frozenset(tool_execution.TOOLS), "editor")
    before = snapshot()
    outcome = asyncio.run(tool_execution.execute_tool(name, json.dumps(data), context)).result
    assert outcome.status == "error" and outcome.code == code and outcome.attempts == 0
    run.assert_not_awaited()
    assert snapshot() == before
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
