import asyncio
import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from io import BytesIO
from time import time
from uuid import uuid4

import pytest
import httpx
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.app import auth, certificate_notifications, certificates, database
from backend.app.certificate_updates import CertificateUpdates, card_topic, owner_topic
from backend.app.main import app


@pytest.fixture
def accounts(client, monkeypatch, tmp_path):
    users = {name: str(uuid4()) for name in ("employee", "other", "issuer", "operator")}
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {"id": users[token], "email": f"{token}@example.test"})
    with database.connect() as connection:
        connection.executemany("INSERT INTO members (user_id, role) VALUES (?, ?)", [
            (user_id, "operator" if name in {"issuer", "operator"} else "reader") for name, user_id in users.items()
        ])
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps({
        "issuer_ids": [users["issuer"]],
        "employees": {
            users["employee"]: {
                "full_name": "Алина Тимурова", "employer": "Учебная компания «Маяк»",
                "position": "Техник", "employed_from": "2024-03-18",
            }
        },
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DESK_TEST_CERTIFICATE_REGISTRY", str(registry_file))
    return {name: {"Authorization": f"Bearer {name}"} for name in users}, users


def create_certificate(client, employee):
    response = client.post("/requests/employment-certificate", json={"purpose": "Для отдела администрирования"}, headers=employee)
    assert response.status_code == 201
    record = response.json()
    assert record["kind"] == "employment_certificate"
    assert record["status"] == "new"
    return "/requests/" + record["id"]


def test_employee_and_issuer_complete_certificate_and_read_it_after_restart(client, accounts):
    headers, users = accounts
    employee, issuer, other, operator = (headers[name] for name in ("employee", "issuer", "other", "operator"))
    assert client.get("/auth/session", headers=employee).json()["member"]["can_request_certificate"]
    assert client.get("/auth/session", headers=issuer).json()["member"]["can_issue_certificate"]
    assert "can_issue_certificate" not in client.get("/auth/session", headers=operator).json()["member"]
    path = create_certificate(client, employee)
    draft, issued = path + "/certificate/draft", path + "/certificate/issued"
    assert client.get(path, headers=other).status_code == 404
    assert client.get(draft).status_code == 401
    assert client.get(draft, headers=employee).status_code == 404
    assert client.get(draft, headers=operator).status_code == 404
    assert client.get(issued, headers=employee).status_code == 404

    response = client.get(draft, headers=issuer)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    draft_text = "\n".join(paragraph.text for paragraph in Document(BytesIO(response.content)).paragraphs)
    for value in ("Алина Тимурова", "Учебная компания «Маяк»", "Техник", "18.03.2024", "Для отдела администрирования"):
        assert value in draft_text
    assert "[ФИО]" not in draft_text
    assert client.patch(path, headers=issuer, json={"expected_status": "new", "status": "in_progress"}).status_code == 409
    assert client.post(path + "/replies", headers=issuer, json={"id": str(uuid4()), "text": "Готово"}).status_code == 409
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    signed = buffer.getvalue()

    def attach(actor, content):
        return client.put(issued, headers={**actor, "Content-Type": "application/pdf"}, content=content)

    assert attach(employee, signed).status_code == 403
    assert attach(operator, signed).status_code == 403
    assert attach(issuer, b"%PDF-not-a-document").status_code == 422
    assert client.get(path, headers=employee).json()["status"] == "new"
    response = attach(issuer, signed)
    assert response.status_code == 200 and response.json()["status"] == "resolved"
    assert attach(issuer, signed).status_code == 200
    assert attach(issuer, signed + b"\n").status_code == 409
    assert client.get(issued, headers=other).status_code == 404
    assert client.get(issued, headers=operator).status_code == 404
    with TestClient(app) as restarted:
        download = restarted.get(issued, headers=employee)
        assert download.status_code == 200 and download.content == signed
        assert download.headers["cache-control"] == "private, no-store"
    with database.connect() as connection:
        audit = connection.execute("SELECT request_id, issued_by FROM request_certificates").fetchall()
    assert len(audit) == 1 and audit[0]["issued_by"] == users["issuer"]


def test_rejects_unregistered_employee_override_and_oversized_pdf(client, accounts, monkeypatch):
    headers, users = accounts
    endpoint = "/requests/employment-certificate"
    assert client.post(endpoint, json={}).status_code == 401
    assert client.post(endpoint, headers=headers["other"], json={}).status_code == 403
    assert client.post(endpoint, headers=headers["employee"], json={"owner_id": users["other"]}).status_code == 422
    assert client.post(endpoint, headers=headers["employee"], json={"purpose": "\x00"}).status_code == 422
    path = create_certificate(client, headers["employee"])
    monkeypatch.setattr(certificates, "MAX_PDF_BYTES", 8)
    issued = path + "/certificate/issued"
    issuer = headers["issuer"]
    assert client.put(issued, headers=issuer, content=b"too large").status_code == 415
    assert client.put(issued, headers={**issuer, "Content-Type": "application/pdf"}, content=b"%PDF-too-large").status_code == 413
    assert client.get(path, headers=headers["employee"]).json()["status"] == "new"


@pytest.mark.parametrize(("topic_for", "event_name"), [(card_topic, "certificate-card"), (owner_topic, "certificate-list")])
def test_certificate_stream_only_wakes_the_matching_scope(topic_for, event_name):
    async def check():
        updates = CertificateUpdates()
        matching, other_topic = topic_for(str(uuid4())), topic_for(str(uuid4()))
        async with aclosing(updates.stream(matching)) as owner, aclosing(updates.stream(other_topic)) as other:
            first = await anext(owner)
            await anext(other)
            untouched = updates.scoped[other_topic].changed
            queue = updates.queue.changed
            updates.publish(matching)
            next_event = await asyncio.wait_for(anext(owner), timeout=1)
            assert next_event.event == event_name and next_event.raw_data != first.raw_data
            assert not untouched.is_set() and not queue.is_set()
        assert not updates.scoped

    asyncio.run(check())


def test_owner_stream_counts_only_unanswered_requests_owned_by_the_session(client, accounts, monkeypatch):
    headers, users = accounts
    updates = client.app.state.certificate_updates
    stream = updates.stream

    async def first_event(topic):
        async with aclosing(stream(topic)) as events:
            yield await anext(events)

    monkeypatch.setattr(updates, "stream", first_event)
    feed = "/requests/certificate-clarification-events?mine=true&user_id=" + users["employee"]

    def snapshot(actor):
        response = client.get(feed, headers=headers[actor])
        assert response.status_code == 200
        assert "event: certificate-list" in response.text
        line = next(line for line in response.text.splitlines() if line.startswith("data: "))
        payload = json.loads(line.removeprefix("data: "))
        assert set(payload) == {"revision", "waiting"} and payload["revision"]
        return payload["waiting"]

    assert snapshot("employee") == 0
    questions = []
    for waiting in (1, 2):
        path = create_certificate(client, headers["employee"]) + "/certificate/clarifications"
        question_id = str(uuid4())
        response = client.post(path, headers=headers["issuer"], json={"id": question_id, "text": "Укажите отдел."})
        assert response.status_code == 200
        questions.append(f"{path}/{question_id}/answer")
        assert snapshot("employee") == waiting
        assert snapshot("other") == snapshot("issuer") == 0

    for remaining, path in zip((1, 0), questions, strict=True):
        response = client.put(path, headers=headers["employee"], json={"text": "Отдел тестирования."})
        assert response.status_code == 200
        assert snapshot("employee") == remaining


def test_clarifications_keep_the_request_and_return_it_to_the_issuer_queue(client, accounts, monkeypatch):
    headers, users = accounts
    employee, issuer, other, operator = (headers[name] for name in ("employee", "issuer", "other", "operator"))
    assert client.patch("/profile", headers=employee, json={"display_name": "Алина"}).status_code == 200
    assert client.patch("/profile", headers=issuer, json={"display_name": "Роман"}).status_code == 200
    monkeypatch.delenv("KNOWLEDGE_DESK_N8N_CERTIFICATE_URL", raising=False)
    path = create_certificate(client, employee)
    request_id = path.split("/")[-1]
    questions = path + "/certificate/clarifications"
    question = {"id": str(uuid4()), "text": "Укажите получателя справки."}
    answer_path = questions + "/" + question["id"] + "/answer"
    answer = {"text": "Для отдела администрирования."}
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf)
    signed = pdf.getvalue()

    feed = "/requests/certificate-clarification-events"
    assert client.get(feed).status_code == 401
    assert client.get(feed, headers=employee).status_code == 403
    assert client.get(feed, headers=operator).status_code == 403
    assert client.get(feed + "?mine=true").status_code == 401
    assert client.get(feed + "?mine=true&request_id=" + request_id, headers=employee).status_code == 422
    scoped_feed = feed + "?request_id=" + request_id
    assert client.get(scoped_feed).status_code == 401
    assert client.get(scoped_feed, headers=other).status_code == 404
    assert client.get(scoped_feed, headers=issuer).status_code == 404
    assert client.get(scoped_feed, headers=operator).status_code == 404
    updates = client.app.state.certificate_updates
    published = []
    publish = updates.publish

    def capture_topic(topic=None):
        published.append(topic)
        publish(topic)

    monkeypatch.setattr(updates, "publish", capture_topic)
    original_revision = updates.queue.revision
    my_list = owner_topic(users["employee"])
    assert client.post(questions, json=question).status_code == 401
    assert client.post(questions, json=question, headers=employee).status_code == 403
    assert client.post(questions, json=question, headers=operator).status_code == 403
    assert client.post(questions, json={**question, "text": "   "}, headers=issuer).status_code == 422
    assert client.post(questions, json={**question, "text": "До\x00после"}, headers=issuer).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(questions, json=question, headers=issuer), range(2)))
    assert all(response.status_code == 200 and response.json()["id"] == request_id for response in responses)
    assert len(responses[0].json()["certificate_clarifications"]) == 1
    assert published == [card_topic(request_id), my_list]
    assert client.post(questions, json={**question, "id": str(uuid4())}, headers=issuer).status_code == 409
    assert client.put(path + "/certificate/issued", headers={**issuer, "Content-Type": "application/pdf"},
                      content=signed).status_code == 409
    assert client.put(answer_path, json=answer, headers=other).status_code == 404
    assert client.put(answer_path, json=answer, headers=issuer).status_code == 404
    assert "certificate_clarifications" not in client.get(path, headers=operator).json()
    assert "certificate_awaiting_reply" not in next(
        record for record in client.get("/requests", headers=operator).json() if record["id"] == request_id
    )

    waiting = client.get(path, headers=employee).json()
    assert waiting["certificate_owned"] and waiting["certificate_awaiting_reply"] and waiting["certificate_can_answer"]
    assert waiting["certificate_clarifications"][0]["question"] == question["text"]
    assert waiting["certificate_clarifications"][0]["question_author"] == {"display_name": "Роман"}
    assert "asked_by" not in waiting["certificate_clarifications"][0]
    assert next(record for record in client.get("/requests", headers=issuer).json()
                if record["id"] == request_id)["certificate_awaiting_reply"]
    assert next(record for record in client.get("/requests", headers=employee).json()
                if record["id"] == request_id)["certificate_awaiting_reply"]
    assert client.get("/requests", headers=other).json() == []
    assert updates.queue.revision == original_revision
    response = client.put(answer_path, json=answer, headers=employee)
    assert response.status_code == 200 and response.json()["status"] == "in_progress"
    assert response.json()["id"] == request_id
    assert "certificate_awaiting_reply" not in response.json()
    assert response.json()["certificate_clarifications"][0]["answer"] == answer["text"]
    assert response.json()["certificate_clarifications"][0]["answer_author"] == {"display_name": "Алина", "is_self": True}
    after_answer = updates.queue.revision
    assert after_answer != original_revision
    assert published == [card_topic(request_id), my_list, None, my_list]
    assert client.put(answer_path, json=answer, headers=employee).status_code == 200
    assert client.put(answer_path, json={"text": "Другой ответ."}, headers=employee).status_code == 409
    assert updates.queue.revision == after_answer
    assert published == [card_topic(request_id), my_list, None, my_list]

    second = {"id": str(uuid4()), "text": "Уточните подразделение."}
    assert client.post(questions, json=second, headers=issuer).status_code == 200
    assert client.put(answer_path, json=answer, headers=employee).json()["certificate_awaiting_reply"]
    assert client.put(questions + "/" + second["id"] + "/answer", json={"text": "Архив."},
                      headers=employee).status_code == 200
    current = next(record for record in client.get("/requests", headers=issuer).json() if record["id"] == request_id)
    assert current["status"] == "in_progress" and "certificate_awaiting_reply" not in current
    assert client.put(path + "/certificate/issued", headers={**issuer, "Content-Type": "application/pdf"},
                      content=signed).json()["status"] == "resolved"
    assert client.post(questions, json={**question, "id": str(uuid4())}, headers=issuer).status_code == 409

    with TestClient(app) as restarted:
        final = restarted.get(path, headers=employee).json()
        assert final["id"] == request_id and final["status"] == "resolved"
        assert [(entry["id"], entry["question"], entry["answer"]) for entry in final["certificate_clarifications"]] == [
            (question["id"], question["text"], answer["text"]), (second["id"], second["text"], "Архив."),
        ]
        assert restarted.get(path + "/certificate/issued", headers=employee).content == signed
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM certificate_notifications").fetchone()[0] == 1


@pytest.mark.parametrize("lose_response", [False, True])
def test_notification_is_issued_once_without_pdf(client, accounts, monkeypatch, lose_response):
    headers, _ = accounts
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_CERTIFICATE_URL",
                       "http://127.0.0.1:5678/webhook/knowledge-desk-certificate-issued")
    monkeypatch.setenv("KNOWLEDGE_DESK_N8N_SECRET", "fixture-secret-that-is-long-enough")
    monkeypatch.setattr(certificate_notifications, "FRONTEND_ORIGIN", "https://desk.example.test")
    calls = []
    email_id = str(uuid4())

    async def post(url, secret, event):
        calls.append(event)
        if lose_response:
            raise httpx.ReadTimeout("Mail response was lost")
        return httpx.Response(200, json={"status": "accepted", "event_id": event["event_id"],
                                        "request_id": event["request_id"], "execution_id": "1",
                                        "provider_email_id": email_id})

    monkeypatch.setattr(certificate_notifications, "post_event", post)
    path = create_certificate(client, headers["employee"])
    issued = path + "/certificate/issued"
    request_id = path.split("/")[-1]
    assert calls == []
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    signed = buffer.getvalue()
    issuer = {**headers["issuer"], "Content-Type": "application/pdf"}
    assert client.put(issued, headers=issuer, content=b"invalid").status_code == 422
    assert calls == []

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.put(issued, headers=issuer, content=signed), range(2)))
    assert all(response.status_code == 200 and response.json()["status"] == "resolved" for response in responses)
    assert client.put(issued, headers=issuer, content=signed).status_code == 200
    assert client.get(issued, headers=headers["employee"]).content == signed
    assert len(calls) == 1
    with database.connect() as connection:
        rows = connection.execute("SELECT event_id, status, provider_email_id FROM certificate_notifications").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == ("unknown" if lose_response else "delivered")
    assert rows[0]["provider_email_id"] == (None if lose_response else email_id)
    detail = client.get(path, headers=headers["employee"]).json()
    assert detail["certificate_email_status"] == ("unknown" if lose_response else "accepted")
    assert calls == [{
        "event_id": rows[0]["event_id"], "request_id": request_id, "type": "certificate.issued",
        "recipient": "employee@example.test", "request_url": f"https://desk.example.test/#requests?id={request_id}",
    }]


def test_resend_webhook_correlates_early_event_and_keeps_terminal_status(client, accounts, monkeypatch):
    headers, _ = accounts
    key = b"test-signing-key-for-certificate-webhook"
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + base64.b64encode(key).decode())
    monkeypatch.delenv("KNOWLEDGE_DESK_N8N_CERTIFICATE_URL", raising=False)
    path = create_certificate(client, headers["employee"])
    request_id = path.split("/")[-1]
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf)
    issue = client.put(path + "/certificate/issued", content=pdf.getvalue(),
                       headers={**headers["issuer"], "Content-Type": "application/pdf"})
    assert issue.status_code == 200 and issue.json()["certificate_email_status"] == "pending"
    event_id = certificate_notifications.claim(request_id)["event_id"]
    email_id = str(uuid4())
    webhook = "/webhooks/resend"

    def send(kind, occurred, *, email=email_id, tagged=True, tamper=False, age=0):
        data = {"email_id": email, "to": ["employee@example.test"]}
        if tagged:
            data["tags"] = {"kd_certificate_event": event_id}
        payload = json.dumps({"type": f"email.{kind}", "created_at": occurred, "data": data}, indent=2)
        timestamp = str(int(time()) - age)
        message = f"msg_test.{timestamp}.{payload}".encode()
        digest = base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()
        return client.post(webhook, content=payload + (" " if tamper else ""), headers={
            "svix-id": "msg_test", "svix-timestamp": timestamp, "svix-signature": f"v1,{digest}",
        })

    def read():
        response = client.get(path, headers=headers["employee"])
        assert response.status_code == 200
        return response.json()["certificate_email_status"]

    delivered_at = "2026-11-21T12:02:00.123Z"
    assert client.post(webhook, json={}).status_code == 400
    assert send("delivered", delivered_at, tamper=True).status_code == 400
    assert send("delivered", delivered_at, age=301).status_code == 400
    assert send("delivered", delivered_at, email=str(uuid4()), tagged=False).status_code == 200
    assert read() == "unknown"
    assert send("delivery_delayed", "2026-11-21T12:01:00Z").status_code == 200
    assert read() == "delayed"
    assert send("delivered", delivered_at, tagged=False).status_code == 200
    assert read() == "delivered"
    certificate_notifications.finish(request_id, "delivered", email_id)
    assert send("delivered", delivered_at).status_code == 200
    assert send("failed", "2026-11-21T12:01:30Z").status_code == 200
    assert send("delivery_delayed", "2026-11-21T12:03:00Z").status_code == 200
    assert read() == "delivered"
    assert send("bounced", "2026-11-21T12:04:00Z").status_code == 200
    assert read() == "not_delivered"
    assert client.get(path, headers=headers["other"]).status_code == 404
    assert client.put(path + "/certificate/issued", content=pdf.getvalue(),
                      headers={**headers["issuer"], "Content-Type": "application/pdf"}).json()[
                          "certificate_email_status"
                      ] == "not_delivered"
    with database.connect() as connection:
        rows = connection.execute("SELECT event_id, provider_email_id FROM certificate_notifications").fetchall()
    assert [(row["event_id"], row["provider_email_id"]) for row in rows] == [(event_id, email_id)]
