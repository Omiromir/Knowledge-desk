import pytest
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.main import FRONTEND_ORIGIN, app


def test_local_frontend_can_read_documents(client):
    response = client.get("/documents", headers={"Origin": FRONTEND_ORIGIN})
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


@pytest.mark.parametrize("origin", ["https://example.com", "http://localhost:5173", "http://127.0.0.1:5174"])
def test_other_origins_fail_preflight(client, origin):
    response = client.options("/requests", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert "Disallowed CORS origin" in response.text


def test_local_frontend_can_submit_requests(client):
    response = client.options("/requests", headers={
        "Origin": FRONTEND_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]


def test_patch_rejects_unknown_fields(client):
    response = client.patch("/documents/missing", json={"unexpected": "value"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("origin,allowed", [(FRONTEND_ORIGIN, True), ("https://example.com", False)])
def test_unexpected_error_keeps_safe_body_and_cors(client, monkeypatch, caplog, origin, allowed):
    def fail():
        raise RuntimeError("Private database detail")

    monkeypatch.setattr(database, "list_documents", fail)
    with TestClient(app, raise_server_exceptions=False) as error_client:
        response = error_client.get("/documents", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert (response.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN) is allowed
    assert "RuntimeError" in caplog.text
    assert "Private database detail" not in caplog.text
