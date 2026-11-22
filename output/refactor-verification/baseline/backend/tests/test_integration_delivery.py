import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import consultation_store, database, integration_delivery as delivery, tool_execution
from backend.app.main import create_app

INPUT = {"name": "Тестовый автор", "question": "Учебный вопрос о консультации"}
TEST_URL = "http://127.0.0.1:5678/webhook-test/knowledge-desk-request-created"
DEDUP_URL = TEST_URL + "-v2"
FIXTURE_SECRET = "fixture-secret-only-for-local-mock-0001"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", TEST_URL)
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_SECRET", FIXTURE_SECRET)
    monkeypatch.setattr(tool_execution, "retrieve_question", Mock(return_value=[]))


@pytest.mark.parametrize("path", ["/webhook/knowledge-desk-request-created", "/webhook-test/../webhook/knowledge-desk-request-created"])
def test_webhook_configuration_cannot_select_a_production_route(monkeypatch, path):
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", "http://127.0.0.1:5678" + path)
    assert delivery.webhook_settings() is None


def draft(client):
    response = client.post("/consultations", json=INPUT)
    assert response.status_code == 201
    row = response.json()
    path = "/consultations/" + row["draft_id"]
    prepared = client.post(path + "/prepare", json={"version": row["version"]}).json()
    assert prepared["status"] == "awaiting_confirmation"
    return path, {**INPUT, "version": row["version"]}


def counts():
    with database.connect() as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("requests", "integration_events"))


def mock_receiver(monkeypatch, handler, url=TEST_URL):
    seen = []
    client_class = httpx.AsyncClient

    async def receive(request):
        assert str(request.url) == url and request.method == "POST"
        assert request.headers[delivery.SECRET_HEADER] == FIXTURE_SECRET
        body = json.loads(request.content)
        assert body.keys() == {"event_id", "request_id", "type", "data"}
        assert body["type"] == "request.created" and body["data"] == {"kind": "consultation", "test": True}
        with database.connect() as connection:
            assert connection.execute("SELECT 1 FROM requests WHERE id = ?", (body["request_id"],)).fetchone()
            assert connection.execute("SELECT status FROM integration_events WHERE event_id = ?", (body["event_id"],)).fetchone()[0] == "unknown"
        seen.append(body)
        return await handler(request, body)

    monkeypatch.setattr(delivery.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(receive), **kwargs))
    return seen


def accepted(body):
    return httpx.Response(200, json={"status": "accepted", "event_id": body["event_id"],
                                     "request_id": body["request_id"], "execution_id": "42"})


async def ok(_request, body):
    return accepted(body)


def test_cancellation_and_preview_do_not_queue_or_send(client, monkeypatch):
    calls = mock_receiver(monkeypatch, ok)
    path, data = draft(client)
    assert counts() == (0, 0) and not calls
    result = client.post(path + "/cancel", json={"version": data["version"]}).json()
    assert result["status"] == "cancelled" and result["delivery"] is None
    assert client.post(path + "/confirm", json=data).status_code == 409
    assert counts() == (0, 0) and not calls


def test_matching_receipt_survives_restart_and_repeated_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "persistent.sqlite3")
    sent = mock_receiver(monkeypatch, ok)
    with TestClient(create_app()) as first:
        path, data = draft(first)
        response = first.post(path + "/confirm", json=data)
        assert response.status_code == 200
        record = response.json()
        event = record["delivery"]
        assert event["status"] == "delivered" and event["receipt"]["execution_id"] == "42"
        assert event["event_id"] == event["receipt"]["event_id"] == sent[0]["event_id"]
        assert event["request_id"] == record["request_id"] == event["receipt"]["request_id"]
    with TestClient(create_app()) as restarted:
        request_path = "/requests/" + record["request_id"]
        assert restarted.get(request_path).json()["question"] == INPUT["question"]
        assert restarted.get(request_path + "/delivery").json() == event
        assert restarted.get(path).json()["delivery"] == event
        assert restarted.post(path + "/confirm", json=data).json()["delivery"] == event
    assert len(sent) == 1 and counts() == (1, 1)


@pytest.mark.parametrize(("mode", "status", "reason"), [
    ("http500", "failed", "http_500"),
    ("timeout", "unknown", "timeout"),
    ("lost_response", "unknown", "transport_error"),
    ("wrong_event", "unknown", "invalid_receipt"),
    ("wrong_request", "unknown", "invalid_receipt"),
    ("bad_json", "unknown", "invalid_receipt"),
    ("redirect", "failed", "http_307"),
])
def test_failures_keep_committed_request_and_do_not_resend(client, monkeypatch, mode, status, reason):
    async def handler(request, body):
        if mode == "http500":
            return httpx.Response(500, text=FIXTURE_SECRET)
        if mode == "timeout":
            await asyncio.Event().wait()
        if mode == "lost_response":
            # The receiver has seen the event. Simulate the reply being lost in transport.
            raise httpx.ReadError(FIXTURE_SECRET, request=request)
        if mode == "bad_json":
            return httpx.Response(200, text=FIXTURE_SECRET)
        if mode == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example.test/"})
        key = "event_id" if mode == "wrong_event" else "request_id"
        return accepted({**body, key: str(uuid4())})

    monkeypatch.setattr(delivery, "TIMEOUT_SECONDS", .04)
    calls = mock_receiver(monkeypatch, handler)
    path, data = draft(client)
    response = client.post(path + "/confirm", json=data)
    assert response.status_code == 200 and FIXTURE_SECRET not in response.text
    outcome = response.json()
    event = outcome["delivery"]
    assert outcome["status"] == "submitted" and event["status"] == status and event["reason"] == reason
    assert event["receipt"] is None
    assert client.get("/requests/" + outcome["request_id"]).json()["name"] == INPUT["name"]
    assert client.post(path + "/confirm", json=data).json()["delivery"] == event
    assert len(calls) == 1 and counts() == (1, 1)


def test_parallel_confirmation_during_delivery_claims_one_post(client, monkeypatch):
    started, release = Event(), Event()

    async def handler(_request, body):
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return accepted(body)

    sent = mock_receiver(monkeypatch, handler)
    path, data = draft(client)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, path + "/confirm", json=data)
        try:
            assert started.wait(3)
            second = pool.submit(client.post, path + "/confirm", json=data).result(timeout=3).json()
            assert second["status"] == "submitted" and second["delivery"]["status"] == "unknown"
        finally:
            release.set()
        completed = first.result(timeout=3).json()
    assert completed["request_id"] == second["request_id"]
    assert completed["delivery"]["event_id"] == second["delivery"]["event_id"]
    assert completed["delivery"]["status"] == "delivered" and len(sent) == 1 and counts() == (1, 1)


def test_interrupted_delivery_is_not_replayed_after_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "claim.sqlite3")
    sent = mock_receiver(monkeypatch, ok)
    with TestClient(create_app()) as first:
        path, data = draft(first)
        request = consultation_store.submit_confirmed("local", path.rsplit("/", 1)[-1], data["version"],
                                                       consultation_store.payload_hash(INPUT["name"], INPUT["question"]))
        event, _ = delivery.claim("local", request, None)
    with TestClient(create_app()) as restarted:
        outcome = restarted.post(path + "/confirm", json=data).json()["delivery"]
        assert outcome["event_id"] == event and outcome["status"] == "unknown"
        assert outcome["reason"] == "attempt_started" and outcome["receipt"] is None
    assert not sent and counts() == (1, 1)


def test_manual_retry_reuses_event_after_lost_receipt_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retry.sqlite3")
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL)
    stored = {}

    async def receiver(request, event):
        if event["event_id"] not in stored:
            stored[event["event_id"]] = accepted(event)
            raise httpx.ReadError("Lost receiver reply", request=request)
        return stored[event["event_id"]]

    sent = mock_receiver(monkeypatch, receiver, DEDUP_URL)
    with TestClient(create_app()) as first:
        path, data = draft(first)
        before = first.post(path + "/confirm", json=data).json()["delivery"]
        assert before["status"] == "unknown" and before["can_retry"]
        assert first.post(path + "/confirm", json=data).json()["delivery"] == before
        assert len(sent) == 1
    with TestClient(create_app()) as reopened:
        request_path = f"/requests/{before['request_id']}/delivery"
        monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL.replace("5678", "5679"))
        assert not reopened.get(request_path).json()["can_retry"]
        assert reopened.post(request_path + "/retry").status_code == 409
        monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL)
        retry = reopened.post(request_path + "/retry")
        assert retry.status_code == 200
        after = retry.json()
        assert after["status"] == "delivered" and not after["can_retry"]
        assert after["receipt"] == stored[before["event_id"]].json()
        assert reopened.post(request_path + "/retry").json() == after
    assert len(sent) == 2 and sent[0] == sent[1]
    assert counts() == (1, 1)


def test_parallel_manual_retry_and_unarmed_test_webhook(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL)
    started, release = Event(), Event()

    async def receiver(request, event):
        if len(sent) == 1:
            raise httpx.ReadError("Lost receiver reply", request=request)
        if len(sent) == 2:
            return httpx.Response(404)
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return accepted(event)

    sent = mock_receiver(monkeypatch, receiver, DEDUP_URL)
    path, data = draft(client)
    event = client.post(path + "/confirm", json=data).json()["delivery"]
    retry_path = f"/requests/{event['request_id']}/delivery/retry"
    missed = client.post(retry_path).json()
    assert missed["status"] == "unknown" and missed["reason"] == "http_404" and missed["can_retry"]
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, retry_path)
        try:
            assert started.wait(3)
            second = pool.submit(client.post, retry_path).result(timeout=3)
            assert second.status_code == 409
        finally:
            release.set()
        result = first.result(timeout=3).json()
    assert result["status"] == "delivered" and result["event_id"] == event["event_id"]
    assert len(sent) == 3 and all(body == sent[0] for body in sent) and counts() == (1, 1)


def test_legacy_unknown_cannot_be_retried_after_switching_receiver(client, monkeypatch):
    async def receiver(request, _event):
        raise httpx.ReadError("Lost receiver reply", request=request)

    sent = mock_receiver(monkeypatch, receiver)
    path, data = draft(client)
    event = client.post(path + "/confirm", json=data).json()["delivery"]
    assert event["status"] == "unknown"
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL)
    request_path = f"/requests/{event['request_id']}/delivery"
    assert not client.get(request_path).json()["can_retry"]
    assert client.post(request_path + "/retry").status_code == 409
    assert len(sent) == 1 and counts() == (1, 1)


def test_unfinished_v2_attempt_can_be_retried_after_lease_expires(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", DEDUP_URL)
    sent = mock_receiver(monkeypatch, ok, DEDUP_URL)
    path, data = draft(client)
    request_id = consultation_store.submit_confirmed("local", path.rsplit("/", 1)[-1], data["version"],
                                                     consultation_store.payload_hash(INPUT["name"], INPUT["question"]))
    receiver = delivery.retry_receiver(delivery.webhook_settings())
    event_id, _ = delivery.claim("local", request_id, receiver)
    retry_path = f"/requests/{request_id}/delivery/retry"
    assert client.post(retry_path).status_code == 409 and not sent
    with database.connect(immediate=True) as connection:
        connection.execute("UPDATE integration_events SET attempt_until = 0 WHERE event_id = ?", (event_id,))
    result = client.post(retry_path).json()
    assert result["status"] == "delivered" and result["event_id"] == event_id
    assert len(sent) == 1 and counts() == (1, 1)


def test_unconfigured_delivery_is_pending_and_production_url_is_rejected(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", TEST_URL.replace("/webhook-test/", "/webhook/"))
    sent = mock_receiver(monkeypatch, ok)
    path, data = draft(client)
    outcome = client.post(path + "/confirm", json=data).json()["delivery"]
    assert outcome["status"] == "pending" and not sent and counts() == (1, 1)
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_TEST_URL", TEST_URL)
    same_event = client.post(path + "/confirm", json=data).json()["delivery"]
    assert same_event["status"] == "delivered" and same_event["event_id"] == outcome["event_id"] and len(sent) == 1


def test_requests_replies_and_delivery_use_trusted_identity(client, monkeypatch):
    sent = mock_receiver(monkeypatch, ok)
    actor = "reader-a"
    client.app.dependency_overrides[tool_execution.local_tool_context] = lambda: tool_execution.ToolContext(actor, frozenset(tool_execution.TOOLS))
    try:
        path, data = draft(client)
        record = client.post(path + "/confirm", json=data).json()
        request_path = "/requests/" + record["request_id"]
        reply_id = str(uuid4())
        assert client.get(request_path + "/delivery").json()["status"] == "delivered"
        assert client.post(request_path + "/replies", json={"id": reply_id, "text": "Материал"}).status_code == 200
        owner_record = client.get(request_path).json()
        actor = "reader-b"
        assert client.get(path).status_code == 404
        assert client.get(request_path).status_code == 404
        assert client.get(request_path + "/delivery").status_code == 404
        assert client.post(request_path + "/delivery/retry").status_code == 404
        assert client.patch(request_path, json={"expected_status": "new", "status": "in_progress"}).status_code == 404
        assert client.post(request_path + "/replies", json={"id": str(uuid4()), "text": "Чужой ответ"}).status_code == 404
        assert client.patch(request_path + "/replies/" + reply_id, json={"text": "Изменение"}).status_code == 404
        assert client.delete(request_path + "/replies/" + reply_id).status_code == 404
        other = client.post("/requests", json=INPUT).json()
        assert client.get("/requests/" + other["id"]).status_code == 200
        assert client.get("/requests/" + other["id"] + "/delivery").json() is None
        actor = "reader-a"
        assert client.get("/requests/" + other["id"]).status_code == 404
        assert client.get(request_path).json() == owner_record and "user_id" not in owner_record
    finally:
        client.app.dependency_overrides.clear()
    assert len(sent) == 1 and counts() == (2, 1)
