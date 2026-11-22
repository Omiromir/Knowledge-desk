import json
import logging

from fastapi.testclient import TestClient
from pytest import raises

from backend.app import config, database, main, telemetry
from backend.app.api import health


def test_health_reports_ready_and_index_failure(client, monkeypatch):
    monkeypatch.setattr(health, "index_ready", lambda: True)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "index": "ok"}
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(health, "index_ready", lambda: False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "ok", "index": "unavailable"}


def test_health_handles_database_failure_without_leaking_details(client, monkeypatch):
    def fail():
        raise OSError("private-dsn-with-password")

    monkeypatch.setattr(database, "connect", fail)
    response = client.get("/health")
    assert response.status_code == 503
    assert "private-dsn-with-password" not in response.text
    assert response.json()["database"] == "unavailable"


def test_deployment_fails_closed_if_auth_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_REQUIRE_AUTH", "1")
    monkeypatch.setattr(config, "FRONTEND_ORIGIN", "https://desk.example.test")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "protected.sqlite3")
    monkeypatch.delenv("KNOWLEDGE_DESK_SUPABASE_URL", raising=False)
    monkeypatch.delenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", raising=False)
    with raises(RuntimeError, match="requires Supabase authentication"), TestClient(main.create_app()):
        pass
    assert not database.DB_PATH.exists()


def test_http_trace_uses_server_id_and_keeps_private_input_out(client, caplog):
    caplog.set_level(logging.INFO, logger=telemetry.logger.name)
    private = "private-password-sentinel" * 100
    response = client.post("/auth/login", json={"email": "fixture@example.test", "password": private},
                           headers={"X-Trace-ID": "not-a-server-id"})
    assert response.status_code == 422
    record, = [json.loads(row.message) for row in caplog.records if row.name == telemetry.logger.name]
    assert response.headers["X-Trace-ID"] == record["trace_id"] != "not-a-server-id"
    assert record["route"] == "/auth/login" and record["status"] == 422
    assert private not in response.text and private not in caplog.text
