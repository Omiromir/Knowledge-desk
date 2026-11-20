import json
from uuid import uuid4

from backend.app import auth, database, rag
from backend.tests.test_uploads import local_index, pdf_file  # noqa: F401


def test_private_requests_pdf_and_rag_are_scoped_to_the_signed_in_account(client, local_index, monkeypatch):
    editor, reader = str(uuid4()), str(uuid4())
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    profiles = {"editor-token": {"id": editor, "email": "editor@example.test"},
                "reader-token": {"id": reader, "email": "reader@example.test", "user_metadata": {"role": "operator"}}}
    monkeypatch.setattr(auth, "provider", lambda path, *, token: profiles[token])
    with database.connect() as connection:
        connection.execute("INSERT INTO members (user_id, role) VALUES (?, 'editor'), (?, 'reader')", (editor, reader))

    owner = {"Authorization": "Bearer editor-token"}
    other = {"Authorization": "Bearer reader-token"}
    assert client.get("/documents").status_code == 401
    assert client.get("/auth/session", headers=other).json()["member"]["role"] == "reader"

    secret = "The blue folder contains the private fixture code 73488."
    pdf = pdf_file(secret)
    created = client.post("/documents/upload?filename=private.pdf", content=pdf, headers=owner)
    assert created.status_code == 201
    document_path = "/documents/" + created.json()["id"]
    assert client.get(document_path + "/original.pdf?page=1", headers=owner).status_code == 200
    assert client.get(document_path, headers=other).status_code == 404
    assert client.get(document_path + "/original.pdf?page=1", headers=other).status_code == 404
    assert client.get("/documents", headers=other).json() == []
    assert client.post("/documents/upload?filename=forbidden.pdf", content=pdf, headers=other).status_code == 403

    generations = []

    async def complete(payload, _instructions, _key, schema, *_args, **_kwargs):
        if schema is rag.AnswerVerification:
            return rag.AnswerVerification(missing_parts=[], unsupported_claims=[])
        fragment, = json.loads(payload)["fragments"]
        generations.append(fragment["text"])
        return rag.AskEvidenceDraft(answer=secret, answerable=True, citations=[fragment["chunk_id"]], evidence=[
            rag.AnswerEvidence(question_part="fixture code", chunk_id=fragment["chunk_id"], quote=secret),
        ])

    monkeypatch.setattr(rag, "complete_structured", complete)
    question = {"question": "What is the private fixture code in the blue folder?"}
    visible = client.post("/ask", json=question, headers=owner).json()
    assert visible["answerable"] and secret in visible["answer"]
    chunk_path = "/ask/sources/" + visible["citations"][0]["chunk_id"]
    assert client.get(chunk_path, headers=other).status_code == 404
    hidden = client.post("/ask", json=question, headers=other).json()
    assert not hidden["answerable"] and hidden["citations"] == [] and len(generations) == 1

    assert client.patch(document_path + "/access", json={"shared": True}, headers=owner).status_code == 200
    draft = client.post("/consultations", json={**question, "name": "Reader fixture"}, headers=other).json()
    draft_path = "/consultations/" + draft["draft_id"]
    prepared = client.post(draft_path + "/prepare", json={"version": draft["version"]}, headers=other).json()
    assert prepared["sources"] and secret in prepared["sources"][0]["text"]
    assert client.patch(document_path + "/access", json={"shared": False}, headers=owner).status_code == 200
    assert client.get(draft_path, headers=other).json()["sources"] == []
    assert client.get(chunk_path, headers=other).status_code == 404

    record = client.post("/requests", json={"name": "Author fixture", "question": "Private request", "owner_id": reader},
                         headers=owner)
    assert record.status_code == 201
    request_path = "/requests/" + record.json()["id"]
    assert client.get(request_path, headers=other).status_code == 404
    assert client.get("/requests", headers=other).json() == []
    reply = {"id": str(uuid4()), "text": "Fixture reply"}
    assert client.post(request_path + "/replies", json=reply, headers=other).status_code == 403

    with database.connect() as connection:
        connection.execute("UPDATE members SET role = 'operator' WHERE user_id = ?", (reader,))
    assert client.get(request_path, headers=other).status_code == 200
    assert client.post(request_path + "/replies", json=reply, headers=other).json()["status"] == "resolved"
    assert client.get(document_path, headers=other).status_code == 404


def test_authenticated_writes_reject_cross_origin_requests(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    response = client.post("/auth/logout", headers={"Origin": "https://other.example.test"})
    assert response.status_code == 403
