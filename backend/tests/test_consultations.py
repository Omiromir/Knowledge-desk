import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app import consultation_graph as graph, consultation_store as store, database, tool_execution
from backend.app.main import create_app

INPUT = {"name": "Мира Тестова", "question": "Как выбрать время консультации?"}
HIT = {"chunk_id": "consultation-v1", "doc_id": "consultations", "title": "Консультация", "text": "Выберите время встречи.",
       "distance": .1, "source": "consultations.md", "version_date": "2026-11-08"}


@pytest.fixture(autouse=True)
def local_search(monkeypatch):
    search = Mock(return_value=[HIT])
    monkeypatch.setattr(tool_execution, "retrieve_question", search)
    return search


def counts():
    with database.connect() as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("requests", "consultation_receipts"))


def new(client):
    response = client.post("/consultations", json=INPUT)
    assert response.status_code == 201, response.text
    return response.json()


def path(row):
    return "/consultations/" + row["draft_id"]


def versioned(row):
    return {key: row[key] for key in ("name", "question", "version")}


def prepare(client, draft):
    response = client.post(path(draft) + "/prepare", json={"version": draft["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def confirmation(client, draft):
    return client.post(path(draft) + "/confirm", json=versioned(draft))


async def read_checkpoint(draft):
    async with AsyncSqliteSaver.from_conn_string(str(graph.checkpoint_file())) as saver:
        return await graph.compile_graph(saver).aget_state(graph.graph_config(draft))


def test_awaiting_checkpoint_survives_app_recreation(monkeypatch, tmp_path, local_search):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "consultation.sqlite3")
    with TestClient(create_app()) as client:
        draft = new(client)
        assert draft["status"] == "draft"
        assert confirmation(client, draft).status_code == 409
        preview = prepare(client, draft)
        assert preview["status"] == "awaiting_confirmation"
        assert preview["sources"] == [{key: HIT[key] for key in ("chunk_id", "title", "text")}]
        assert counts() == (0, 0)
    saved = asyncio.run(read_checkpoint(preview))
    assert saved.next == ("review",) and saved.tasks[0].interrupts
    assert saved.values["user_id"] == "local" and saved.values["confirmation"] is None
    assert saved.values["tool_calls"] == 2 and saved.values["steps"] == 3
    with TestClient(create_app()) as restarted:
        assert restarted.get(path(preview)).json() == preview
        assert prepare(restarted, preview) == preview
        rejected = restarted.post(path(preview) + "/confirm", json={"answer": "Я подтверждаю"})
        assert rejected.status_code == 422 and counts() == (0, 0)
        sent = confirmation(restarted, preview).json()
        assert sent["status"] == "submitted" and sent["request_id"]
        assert restarted.get("/requests/" + sent["request_id"]).json()["question"] == INPUT["question"]
        assert counts() == (1, 1)
    local_search.assert_called_once()


def test_edit_invalidates_confirmation_and_changes_operation_key(client):
    first = prepare(client, new(client))
    revision = {**versioned(first), "question": "Есть время в пятницу?"}
    changed = client.patch(path(first), json=revision).json()
    assert changed["draft_id"] == first["draft_id"] and changed["version"] == first["version"] + 1
    assert changed["status"] == "draft"
    assert store.operation_key(first["draft_id"], first["version"]) != store.operation_key(changed["draft_id"], changed["version"])
    old = confirmation(client, first)
    assert old.status_code == 409 and old.json()["detail"]["code"] == "stale_version"
    assert confirmation(client, changed).status_code == 409
    assert counts() == (0, 0)
    preview = prepare(client, changed)
    sent = confirmation(client, preview).json()
    assert sent["question"] == revision["question"] and sent["status"] == "submitted"
    with database.connect() as connection:
        assert connection.execute("SELECT operation_key FROM consultation_receipts").fetchone()[0] == store.operation_key(changed["draft_id"], changed["version"])


def test_cancelled_review_cannot_submit(client):
    preview = prepare(client, new(client))
    for _ in range(2):
        cancelled = client.post(path(preview) + "/cancel", json={"version": preview["version"]})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert confirmation(client, preview).status_code == 409
    assert client.get(path(preview)).json()["question"] == INPUT["question"]
    assert counts() == (0, 0)


def test_partial_preparation_from_previous_process_stops_without_more_tools(client, local_search):
    draft = new(client)

    async def leave_partial_checkpoint():
        row = store.get_draft("local", draft["draft_id"])
        async with AsyncSqliteSaver.from_conn_string(str(graph.checkpoint_file())) as saver:
            await graph.compile_graph(saver).aupdate_state(graph.graph_config(row), {**row, "steps": 1, "tool_calls": 1}, as_node="search")

    asyncio.run(leave_partial_checkpoint())
    stopped = prepare(client, draft)
    assert stopped["status"] == "failed" and stopped["stop_code"] == "preparation_interrupted"
    assert stopped["question"] == INPUT["question"] and counts() == (0, 0)
    local_search.assert_not_called()


def test_cancel_running_search_prevents_next_tool(client, monkeypatch):
    started, release = Event(), Event()

    async def pending_search(_):
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return {"fragments": []}

    draft_tool = AsyncMock()
    monkeypatch.setitem(tool_execution.TOOLS, "search_documents", replace(tool_execution.TOOLS["search_documents"], run=pending_search))
    monkeypatch.setitem(tool_execution.TOOLS, "prepare_request", replace(tool_execution.TOOLS["prepare_request"], run=draft_tool))
    draft = new(client)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(prepare, client, draft)
        try:
            assert started.wait(2)
            cancelled = client.post(path(draft) + "/cancel", json={"version": draft["version"]})
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        finally:
            release.set()
        assert future.result(timeout=4)["status"] == "cancelled"
    draft_tool.assert_not_awaited()
    assert counts() == (0, 0)


@pytest.mark.parametrize(("node", "stop_code", "steps", "calls"), [
    ("preview", "step_limit", 8, 2),
    ("search", "tool_limit", 3, 3),
])
def test_intentional_loop_stops_and_preserves_draft(client, monkeypatch, caplog, node, stop_code, steps, calls):
    original = getattr(graph, node)

    async def repeat(state, run):
        update, _ = await original(state, run)
        return update, node

    monkeypatch.setattr(graph, node, repeat)
    caplog.set_level(logging.INFO, logger=graph.logger.name)
    stopped = prepare(client, new(client))
    assert stopped["status"] == "failed" and stopped["stop_code"] == stop_code
    assert stopped["question"] == INPUT["question"] and stopped["sources"]
    checkpoint = asyncio.run(read_checkpoint(stopped))
    assert checkpoint.values["steps"] == steps and checkpoint.values["tool_calls"] == calls
    assert not checkpoint.next and stop_code in caplog.text
    assert confirmation(client, stopped).status_code == 409 and counts() == (0, 0)


@pytest.mark.parametrize(("timeout_layer", "code"), [("tool", "tool_timeout"), ("graph", "active_timeout")])
def test_timeouts_keep_draft_and_no_request(client, monkeypatch, timeout_layer, code):
    async def no_network_reply(_):
        await asyncio.Event().wait()

    mock = AsyncMock(side_effect=no_network_reply)
    monkeypatch.setitem(tool_execution.TOOLS, "search_documents", replace(tool_execution.TOOLS["search_documents"], run=mock))
    if timeout_layer == "tool":
        monkeypatch.setattr(tool_execution, "TOOL_TIMEOUT", .02)
    else:
        monkeypatch.setattr(graph, "CONSULTATION_ACTIVE_SECONDS", .4)
    result = prepare(client, new(client))
    assert result["status"] == "failed" and result["stop_code"] == code
    assert result["question"] == INPUT["question"] and result["request_id"] is None
    mock.assert_awaited_once()
    assert counts() == (0, 0)


def test_parallel_confirmation_and_lost_response_are_idempotent(client):
    preview = prepare(client, new(client))
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: confirmation(client, preview), range(2)))
    assert all(response.status_code == 200 for response in responses)
    # Simulate a client that received neither response and repeats its original payload.
    repeated = confirmation(client, preview).json()
    assert repeated["request_id"] and repeated["status"] == "submitted"
    assert {response.json()["request_id"] for response in responses} == {repeated["request_id"]}
    different_payload = client.post(path(preview) + "/confirm", json={**versioned(preview), "question": "Другие данные"})
    assert different_payload.status_code == 409 and different_payload.json()["detail"]["code"] == "payload_conflict"
    assert counts() == (1, 1)


def test_parallel_sql_transactions_are_idempotent_without_api_lock(client):
    draft = prepare(client, new(client))
    digest = store.payload_hash(draft["name"], draft["question"])
    both_ready = Barrier(2)

    def submit(_):
        both_ready.wait(timeout=3)
        return store.submit_confirmed("local", draft["draft_id"], draft["version"], digest)

    with ThreadPoolExecutor(2) as pool:
        assert len(set(pool.map(submit, range(2)))) == 1
    assert counts() == (1, 1)


def test_receipt_survives_error_after_commit_before_checkpoint(client, monkeypatch):
    draft = prepare(client, new(client))
    submit = store.submit_confirmed

    def committed_then_stopped(*args):
        submit(*args)
        raise TimeoutError

    monkeypatch.setattr(store, "submit_confirmed", committed_then_stopped)
    sent = confirmation(client, draft).json()
    assert sent["status"] == "submitted" and sent["request_id"]
    checkpoint = asyncio.run(read_checkpoint(draft))
    assert checkpoint.next == ("submit",) and not checkpoint.values.get("request_id")
    assert confirmation(client, draft).json()["request_id"] == sent["request_id"]
    assert counts() == (1, 1)


def test_request_write_rolls_back_when_receipt_fails(client):
    draft = prepare(client, new(client))
    with database.connect(immediate=True) as connection:
        connection.execute("CREATE TRIGGER broken_receipt BEFORE INSERT ON consultation_receipts "
                           "BEGIN SELECT RAISE(ABORT, 'fixture receipt failure'); END")
    response = confirmation(client, draft)
    assert response.status_code == 200
    assert response.json()["status"] == "failed" and response.json()["stop_code"] == "storage_failed"
    assert response.json()["question"] == INPUT["question"] and counts() == (0, 0)


def test_draft_and_receipt_are_private_to_server_user(client):
    draft = prepare(client, new(client))
    assert confirmation(client, draft).json()["status"] == "submitted"
    client.app.dependency_overrides[tool_execution.local_tool_context] = lambda: tool_execution.ToolContext(
        user_id="other-user", allowed_tools=frozenset(tool_execution.TOOLS),
    )
    try:
        assert client.get(path(draft)).status_code == 404
        assert confirmation(client, draft).status_code == 404
    finally:
        client.app.dependency_overrides.clear()
    assert counts() == (1, 1)
