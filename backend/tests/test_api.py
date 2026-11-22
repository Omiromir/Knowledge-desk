import sqlite3
from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

import pytest

from backend.app import database
from backend.app.domain import RequestReply

DOCUMENT = {
    "title": "Учебный документ",
    "text": "Текст с кавычкой: O'Connor.",
    "source": "pytest",
}


@pytest.fixture
def document(client):
    response = client.post("/documents", json=DOCUMENT)
    assert response.status_code == 201
    record = response.json()
    assert record == DOCUMENT | {"id": record["id"]}
    return record


def test_create_document(client):
    response = client.post("/documents", json=DOCUMENT)

    assert response.status_code == 201
    record = response.json()
    assert UUID(record["id"]).version == 4
    assert record == DOCUMENT | {"id": record["id"]}


def test_request_queries_ignore_new_database_columns(client):
    with database.connect() as connection:
        connection.execute("ALTER TABLE requests ADD COLUMN future_metadata TEXT")
    request = client.post("/requests", json={"name": "Ирина", "question": "Проверка списка"}).json()
    path = f"/requests/{request['id']}"
    assert client.get("/requests").json() == [request]
    assert client.get(path).json() == request
    status = client.patch(path, json={"expected_status": "new", "status": "in_progress"})
    assert status.status_code == 200
    reply = client.post(path + "/replies", json={"id": str(uuid4()), "text": "Проверено"})
    assert reply.status_code == 200 and reply.json()["status"] == "resolved"


def test_read_document(client, document):
    response = client.get(f"/documents/{document['id']}")

    assert response.status_code == 200
    assert response.json() == document


def test_list_documents(client):
    response = client.get("/documents")
    assert response.status_code == 200
    assert response.json() == []

    created = client.post("/documents", json=DOCUMENT)
    assert created.status_code == 201
    response = client.get("/documents")
    assert response.status_code == 200
    assert response.json() == [DOCUMENT | {"id": created.json()["id"]}]


def test_update_document_saves_changes(client, document):
    updates = {"title": "Новое название", "text": "Новый текст"}
    expected = document | updates
    response = client.patch(f"/documents/{document['id']}", json=updates)

    assert response.status_code == 200
    assert response.json() == expected
    saved = client.get(f"/documents/{document['id']}")
    assert saved.status_code == 200
    assert saved.json() == expected
    assert asdict(database.get_document(document["id"])) == expected


def test_delete_document(client, document):
    response = client.delete(f"/documents/{document['id']}")

    assert response.status_code == 204
    assert response.content == b""
    missing = client.get(f"/documents/{document['id']}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Document not found."}
    assert database.get_document(document["id"]) is None


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_reject_empty_document_text(client, text):
    response = client.post("/documents", json=DOCUMENT | {"text": text})

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "text"]
    assert error["type"] == "string_too_short"
    saved = client.get("/documents")
    assert saved.status_code == 200
    assert saved.json() == []


@pytest.mark.parametrize(
    "method,path,detail",
    [
        ("GET", "/documents/missing", "Document not found."),
        ("PATCH", "/documents/missing", "Document not found."),
        ("DELETE", "/documents/missing", "Document not found."),
        ("GET", "/requests/missing", "Request not found."),
    ],
)
def test_unknown_id_returns_404(client, method, path, detail):
    response = client.request(
        method, path, json={"title": "Новое название"} if method == "PATCH" else None
    )

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


def test_create_request(client):
    payload = {"name": "О'Коннор", "question": "Как сохранить документ?"}
    response = client.post("/requests", json=payload)

    assert response.status_code == 201
    record = response.json()
    assert UUID(record["id"]).version == 4
    assert datetime.fromisoformat(record["created_at"]).utcoffset().total_seconds() == 0
    assert record == payload | {"id": record["id"], "status": "new", "created_at": record["created_at"]}
    saved = client.get(f"/requests/{record['id']}")
    assert saved.status_code == 200
    assert saved.json() == record


def test_request_status_workflow_preserves_fields_and_checks_stale_updates(client, document):
    payload = {"name": "Reader", "question": "Where is the source?", "document_id": document["id"]}
    created = client.post("/requests", json=payload)
    assert created.status_code == 201 and created.json()["status"] == "new"
    record = created.json()
    path = f"/requests/{record['id']}"

    def change(expected, status):
        return client.patch(path, json={"expected_status": expected, "status": status})

    skipped = change("new", "resolved")
    assert skipped.status_code == 409 and client.get(path).json() == record
    for expected, status in [("new", "in_progress"), ("new", "in_progress"), ("in_progress", "resolved")]:
        changed = change(expected, status)
        assert changed.status_code == 200 and changed.json() == {**record, "status": status}
        assert client.get(path).json() == changed.json()

    stale = change("new", "in_progress")
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "request_status_conflict"
    assert client.get(path).json()["status"] == "resolved"
    reopened = change("resolved", "in_progress")
    assert reopened.status_code == 200 and reopened.json() == {**record, "status": "in_progress"}
    assert change("in_progress", "arbitrary").status_code == 422
    missing = client.patch("/requests/missing", json={"expected_status": "new", "status": "in_progress"})
    assert missing.status_code == 404 and missing.json()["detail"] == "Request not found."


def test_reject_request_without_question(client):
    response = client.post("/requests", json={"name": "Тестовый пользователь"})

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "question"]
    assert error["type"] == "missing"


def test_request_reply_closes_once_and_keeps_history_after_reopening(client, document):
    created = client.post("/requests", json={
        "name": "Reader", "question": "Question", "document_id": document["id"],
    }).json()
    path = f"/requests/{created['id']}"
    reply = {"id": str(uuid4()), "text": "  Срок гарантии — 37 дней.  "}
    assert client.post(f"{path}/replies", json={**reply, "text": "  "}).status_code == 422
    assert client.get(path).json() == created

    sent = client.post(f"{path}/replies", json=reply)
    assert sent.status_code == 200
    result = sent.json()
    assert result["status"] == "resolved" and result["document_id"] == document["id"]
    entry, = result["replies"]
    assert entry["id"] == reply["id"] and entry["text"] == reply["text"].strip()
    assert datetime.fromisoformat(entry["created_at"]).utcoffset().total_seconds() == 0
    assert client.get(path).json() == result
    assert client.post(f"{path}/replies", json=reply).json() == result
    assert client.post(f"{path}/replies", json={**reply, "text": "Different"}).status_code == 409

    followup = {"id": str(uuid4()), "text": "Уточнение к ответу."}
    assert client.post(f"{path}/replies", json=followup).status_code == 409
    reopened = client.patch(path, json={"expected_status": "resolved", "status": "in_progress"}).json()
    assert reopened["replies"] == [entry]
    assert client.post(f"{path}/replies", json=reply).json() == reopened
    later = client.post(f"{path}/replies", json=followup).json()
    assert later["status"] == "resolved"
    assert [item["id"] for item in later["replies"]] == [reply["id"], followup["id"]]
    assert client.post("/requests/missing/replies", json=followup).status_code == 404


def test_request_reply_rolls_back_when_closing_fails(client):
    created = client.post("/requests", json={"name": "Reader", "question": "Question"}).json()
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_close BEFORE UPDATE ON requests
               BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.add_request_reply(created["id"], RequestReply(str(uuid4()), "Answer", "2026-11-09T00:00:00+00:00"))
    assert client.get(f"/requests/{created['id']}").json() == created


def test_edit_and_delete_reply_in_legacy_history(client):
    request = client.post("/requests", json={"name": "Reader", "question": "Question"}).json()
    other = client.post("/requests", json={"name": "Other", "question": "Another question"}).json()
    path = f"/requests/{request['id']}"
    reply = {"id": str(uuid4()), "text": "Initial answer"}
    sent = client.post(f"{path}/replies", json=reply).json()
    url = f"{path}/replies/{reply['id']}"

    with database.connect() as connection:
        connection.execute("ALTER TABLE request_replies DROP COLUMN edited_at")
        connection.execute("ALTER TABLE request_replies DROP COLUMN deleted")
    database.init_db()
    assert client.get(path).json() == sent
    assert client.patch(url, json={"text": "  "}).status_code == 422
    assert client.delete(f"/requests/{other['id']}/replies/{reply['id']}").status_code == 404
    edited = client.patch(url, json={"text": " Updated answer "})
    assert edited.status_code == 200
    record = edited.json()
    entry, = record["replies"]
    assert entry["text"] == "Updated answer" and record["status"] == "resolved"
    assert entry["created_at"] == sent["replies"][0]["created_at"]
    assert datetime.fromisoformat(entry["edited_at"]).utcoffset().total_seconds() == 0
    assert client.patch(url, json={"text": "Updated answer"}).json() == record
    assert client.get(path).json() == record

    deleted = client.delete(url)
    assert deleted.status_code == 200
    assert deleted.json() == {**request, "status": "resolved"}
    assert client.delete(url).json() == deleted.json()
    assert client.patch(url, json={"text": "Restored?"}).status_code == 404
    assert client.get(path).json() == deleted.json()
    assert client.patch(path, json={"expected_status": "resolved", "status": "in_progress"}).status_code == 200
    assert client.post(f"{path}/replies", json=reply).status_code == 409


def test_document_request_keeps_question_when_document_is_deleted(client, document):
    document_id = document["id"]
    payload = {"name": "Reader", "question": "Where is the source?", "document_id": document_id}
    response = client.post("/requests", json=payload)
    assert response.status_code == 201
    record = response.json()
    assert record == {**payload, "id": record["id"], "status": "new", "created_at": record["created_at"]}
    path = f"/requests/{record['id']}"
    assert client.get(path).json() == record
    assert client.delete(f"/documents/{document_id}").status_code == 204
    saved = client.get(path)
    assert saved.status_code == 200
    assert saved.json() == {key: value for key, value in record.items() if key != "document_id"}


def test_reject_request_for_missing_document(client):
    response = client.post("/requests", json={
        "name": "Reader", "question": "Where is the source?", "document_id": "missing",
    })
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found."}
