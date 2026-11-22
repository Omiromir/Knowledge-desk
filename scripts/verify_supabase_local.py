"""Smoke-test a disposable Supabase stack on the refactor verification ports."""

import asyncio
import json
import os
import sys
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def verify(settings: dict, temporary: Path) -> None:
    origin = settings["API_URL"].rstrip("/")
    db_url = settings["DB_URL"]
    db_address = conninfo_to_dict(db_url)
    if origin != "http://127.0.0.1:55321" or (
        db_address.get("host"), db_address.get("port"), db_address.get("dbname"),
    ) not in {
        ("127.0.0.1", "55322", "postgres"), ("localhost", "55322", "postgres"),
    }:
        raise ValueError("Expected the isolated Supabase stack on local ports 55321 and 55322")
    public_key = settings.get("ANON_KEY") or settings["PUBLISHABLE_KEY"]
    admin_key = settings.get("SERVICE_ROLE_KEY") or settings["SECRET_KEY"]
    suffix = uuid4().hex[:10]
    accounts = {}
    with httpx.Client(base_url=origin, trust_env=False, timeout=10) as provider:
        for role in ("reader", "other", "editor", "storage"):
            credentials = {"email": f"{role}-{suffix}@example.test", "password": token_urlsafe(24)}
            result = provider.post("/auth/v1/admin/users",
                                   headers={"apikey": admin_key, "Authorization": f"Bearer {admin_key}"},
                                   json={**credentials, "email_confirm": True,
                                         "app_metadata": {"knowledge_desk_storage": "originals"} if role == "storage" else {}})
            assert result.is_success, (role, result.status_code)
            accounts[role] = (result.json()["id"], credentials)

    login = f"smoke_{suffix}"
    db_password = token_urlsafe(24)
    with psycopg.connect(db_url, connect_timeout=5) as connection:
        count, = connection.execute("SELECT COUNT(*) FROM supabase_migrations.schema_migrations").fetchone()
        assert count == len(list(Path("supabase/migrations").glob("*.sql"))), "Migration count differs from the source tree"
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(login), sql.Literal(db_password)))
        connection.execute(sql.SQL("GRANT knowledge_desk_runtime TO {}").format(sql.Identifier(login)))
        for role in ("reader", "other", "editor"):
            connection.execute(
                "INSERT INTO knowledge_desk.members (user_id, role, can_operate_requests, certificate_issuer)"
                " VALUES (%s, %s, %s, %s)",
                (accounts[role][0], "editor" if role == "editor" else "reader", role == "editor", role == "editor"),
            )
    reader_id, reader_credentials = accounts["reader"]
    _, editor_credentials = accounts["editor"]
    _, other_credentials = accounts["other"]
    _, storage_credentials = accounts["storage"]
    registry = temporary / "registry.json"
    registry.write_text(json.dumps({"issuer_ids": [], "employees": {reader_id: {
        "full_name": "Учебный сотрудник", "employer": "Учебная команда",
        "position": "Сотрудник", "employed_from": "2025-04-18",
    }}}), encoding="utf-8")
    os.environ.update(
        KNOWLEDGE_DESK_REQUIRE_AUTH="1",
        KNOWLEDGE_DESK_FRONTEND_ORIGIN="https://desk.example.test",
        KNOWLEDGE_DESK_STATIC_DIR="frontend/dist",
        KNOWLEDGE_DESK_DATABASE_URL=make_conninfo(db_url, user=login, password=db_password),
        KNOWLEDGE_DESK_SUPABASE_URL=origin,
        KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY=public_key,
        KNOWLEDGE_DESK_STORAGE_EMAIL=storage_credentials["email"],
        KNOWLEDGE_DESK_STORAGE_PASSWORD=storage_credentials["password"],
        KNOWLEDGE_DESK_TEST_CERTIFICATE_REGISTRY=str(registry),
        KNOWLEDGE_DESK_N8N_TEST_URL="http://127.0.0.1:55329/webhook-test/knowledge-desk-request-created-v2",
        KNOWLEDGE_DESK_N8N_SECRET=token_urlsafe(32),
    )
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    from fastapi.testclient import TestClient
    from pypdf import PdfReader

    from backend.app import certificate_notifications, integration_delivery, rag, tool_execution, uploads
    from backend.app.main import create_app
    from backend.tests.test_uploads import pdf_file

    def sign_in(client, credentials):
        login_response = client.post("/auth/login", json=credentials)
        assert login_response.status_code == 200, login_response.status_code
        assert "Secure" in login_response.headers["set-cookie"]

    def embed(texts, _prefix):
        return [[1.0] + [0.0] * 383 for _ in texts]

    deliveries = []

    async def receive(_url, _secret, payload):
        deliveries.append(payload)
        if len(deliveries) == 1:
            raise httpx.ReadError("Fixture: lost receipt")
        return httpx.Response(200, json={
            "status": "accepted", "event_id": payload["event_id"], "request_id": payload["request_id"],
            "execution_id": "42",
        })

    with (
        patch.object(uploads, "embed", embed),
        patch.object(rag, "embed", embed),
        patch.object(tool_execution, "retrieve_question", return_value=[]),
        patch.object(integration_delivery, "post_event", side_effect=receive),
        patch.object(certificate_notifications, "deliver"),
        TestClient(create_app(), base_url="https://desk.example.test") as client,
    ):
        assert client.get("/health").json()["status"] == "ready"
        assert client.get("/").status_code == 200
        assert client.get("/requests").status_code == 401
        sign_in(client, editor_credentials)
        uploaded = client.post("/documents/upload?filename=internal.pdf",
                               content=pdf_file("Internal test policy", pages=2),
                               headers={"content-type": "application/pdf"})
        assert uploaded.status_code == 201, uploaded.status_code
        doc_id = uploaded.json()["id"]
        original = f"/documents/{doc_id}/original.pdf"
        page = client.get(original + "?page=2")
        assert page.status_code == 200 and len(PdfReader(BytesIO(page.content)).pages) == 1
        assert any(hit["doc_id"] == doc_id for hit in rag.retrieve_question("policy", user_id=accounts["editor"][0]))
        sign_in(client, reader_credentials)
        assert client.get(original).status_code == 404
        assert not any(hit["doc_id"] == doc_id for hit in rag.retrieve_question("policy", user_id=reader_id))

        cert = client.post("/requests/employment-certificate", json={"purpose": "Учебная проверка"})
        assert cert.status_code == 201, cert.status_code
        path = f"/requests/{cert.json()['id']}"
        sign_in(client, other_credentials)
        assert client.get(path).status_code == 404
        assert client.get(path + "/certificate/issued").status_code == 404
        sign_in(client, editor_credentials)
        assert client.get(path + "/certificate/draft").status_code == 200
        signed = pdf_file()
        issued = client.put(path + "/certificate/issued", content=signed, headers={"content-type": "application/pdf"})
        assert issued.status_code == 200 and issued.json()["status"] == "resolved"
        sign_in(client, reader_credentials)
        assert client.get(path + "/certificate/issued").content == signed

        input_data = {"name": "Учебный сотрудник", "question": "Учебный вопрос о консультации"}
        draft = client.post("/consultations", json=input_data)
        assert draft.status_code == 201, draft.status_code
        draft_data = draft.json()
        draft_path = f"/consultations/{draft_data['draft_id']}"
        version = {"version": draft_data["version"]}
        prepared = client.post(draft_path + "/prepare", json=version)
        assert prepared.status_code == 200 and prepared.json()["status"] == "awaiting_confirmation"
        event = client.post(draft_path + "/confirm", json={**input_data, **version}).json()["delivery"]
        assert event["status"] == "unknown" and event["can_retry"]
        retry_url = f"/requests/{event['request_id']}/delivery/retry"
        retried = client.post(retry_url).json()
        assert retried["status"] == "delivered" and retried["event_id"] == event["event_id"]
        assert client.post(retry_url).json() == retried and deliveries == [deliveries[0], deliveries[0]]

    print(f"Supabase smoke passed ({count} migrations, protected HTTP, private Storage, pgvector, certificates, retry).")


if __name__ == "__main__":
    for key in tuple(os.environ):
        if key.startswith(("KNOWLEDGE_DESK_", "OPENAI_", "RESEND_", "N8N_")):
            os.environ.pop(key, None)
    with TemporaryDirectory(prefix="kd-refactor-") as directory:
        verify(json.load(sys.stdin), Path(directory))
