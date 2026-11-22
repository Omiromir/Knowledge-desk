from uuid import uuid4

from backend.app import auth, database


def test_reply_drafts_are_private_versioned_and_cleared_with_the_reply(client, monkeypatch):
    users = {name: str(uuid4()) for name in ("first", "second", "reader")}
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {"id": users[token], "email": f"{token}@example.test"})
    with database.connect() as connection:
        connection.executemany("INSERT INTO members (user_id, role) VALUES (?, ?)", [
            (user_id, "reader" if name == "reader" else "operator") for name, user_id in users.items()
        ])

    headers = {name: {"Authorization": f"Bearer {name}"} for name in users}
    first = headers["first"]
    second = headers["second"]
    reader = headers["reader"]

    def create_request():
        response = client.post("/requests", json={"name": "Reader", "question": "A shared operator case"}, headers=reader)
        assert response.status_code == 201
        return "/requests/" + response.json()["id"]

    def save(path, actor, text, expected=None):
        body = {"revision": str(uuid4()), "expected_revision": expected, "text": text}
        return client.put(path, headers=actor, json=body), body

    request_path = create_request()
    draft_path = request_path + "/reply-draft"
    other_path = create_request() + "/reply-draft"
    assert client.get(draft_path).status_code == 401
    assert client.get(draft_path, headers=reader).status_code == 403
    assert client.get("/requests/missing/reply-draft", headers=first).status_code == 404
    assert client.put(draft_path, headers={**first, "Origin": "https://foreign.example.test"}, json={}).status_code == 403
    original, sent = save(draft_path, first, "  First operator draft  ")
    assert original.status_code == 200
    assert original.json() == {"text": sent["text"], "revision": sent["revision"]}
    assert client.get(draft_path, headers=second).json() is None
    assert client.put(draft_path, headers=second, json={**sent, "operator_id": users["first"]}).status_code == 422
    assert client.put(draft_path, headers=reader, json=sent).status_code == 403
    assert client.put(draft_path, headers=first, json=sent).status_code == 200
    assert client.get(draft_path, headers=first).json() == original.json()
    assert save(draft_path, first, "Stale browser copy")[0].status_code == 409
    another_operator, _ = save(draft_path, second, "Independent draft")
    another_request, _ = save(other_path, first, "Other request")
    reply = {"id": sent["revision"], "text": sent["text"].strip()}
    assert client.post(request_path + "/replies", headers=reader, json=reply).status_code == 403
    assert client.get(draft_path, headers=first).json() == original.json()
    posted = client.post(request_path + "/replies", headers=first, json=reply)
    assert posted.status_code == 200 and posted.json()["status"] == "resolved"
    assert client.get(draft_path, headers=first).json() is None
    assert client.get(draft_path, headers=second).json() == another_operator.json()
    assert client.get(other_path, headers=first).json() == another_request.json()
    assert client.put(draft_path, headers=first, json=sent).status_code == 409

    newer, _ = save(draft_path, first, "New draft written on a second device")
    retry = client.post(request_path + "/replies", headers=first, json=reply)
    assert len(retry.json()["replies"]) == 1
    assert client.get(draft_path, headers=first).json() == newer.json()
    assert save(draft_path, first, "", newer.json()["revision"])[0].json() is None
