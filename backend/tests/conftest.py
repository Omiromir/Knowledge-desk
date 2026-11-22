import pytest
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(app) as test_client:
        yield test_client
