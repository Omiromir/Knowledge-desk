from hashlib import sha256
from unittest.mock import Mock
from uuid import uuid4

from backend.app import auth, database, originals
from backend.app.storage import documents as document_store
from backend.tests.test_uploads import pdf_file


def test_original_route_checks_access_before_fetching_storage(client, monkeypatch):
    editor, reader = str(uuid4()), str(uuid4())
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {"id": token, "email": "fixture@example.test"})
    with database.connect() as connection:
        connection.execute("INSERT INTO members (user_id, role) VALUES (?, 'editor'), (?, 'reader')", (editor, reader))

    owner_headers = {"Authorization": f"Bearer {editor}"}
    reader_headers = {"Authorization": f"Bearer {reader}"}
    created = client.post("/documents", json={"title": "Fixture", "text": "Private", "source": "sample.pdf"},
                          headers=owner_headers)
    path = f"/documents/{created.json()['id']}/original.pdf"
    content = pdf_file("Original page", pages=2)
    key = f"{uuid4().hex}.pdf"
    get_pdf = Mock(return_value={"filename": "sample.pdf", "content": None, "object_key": key,
                                 "content_sha256": sha256(content).hexdigest()})
    fetch = Mock(return_value=content)
    monkeypatch.setattr(document_store, "get_pdf", get_pdf)
    monkeypatch.setattr(originals, "storage_request", fetch)

    assert client.get(path).status_code == 401
    assert client.get(path + "?page=2", headers=reader_headers).status_code == 404
    get_pdf.assert_not_called()
    fetch.assert_not_called()

    result = client.get(path, headers=owner_headers)
    assert result.status_code == 200 and result.content == content
    assert result.headers["cache-control"] == "private, no-store" and "location" not in result.headers
    fetch.assert_called_once_with("GET", f"object/authenticated/{originals.BUCKET}/{key}")
    fetch.return_value = b"Unexpected bytes"
    failed = client.get(path, headers=owner_headers)
    assert failed.status_code == 503 and key not in failed.text
