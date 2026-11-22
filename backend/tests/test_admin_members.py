from types import SimpleNamespace
from uuid import uuid4

from backend.app import admin_auth, auth, certificates, database
from backend.app.api import admin


def test_admin_routes_ignore_user_metadata_and_require_server_grant(client, monkeypatch):
    uid = str(uuid4())
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {
        "id": uid, "email": "editor@example.test", "user_metadata": {"is_admin": True},
    })
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO members (user_id, role, can_operate_requests) VALUES (?, 'editor', TRUE)", (uid,),
        )

    def unexpected(*args, **kwargs):
        raise AssertionError("Unauthorized request reached an administrative operation")

    monkeypatch.setattr(admin_auth, "create_user", unexpected)
    monkeypatch.setattr(admin, "directory_rows", unexpected)
    monkeypatch.setattr(admin, "change_member", unexpected)
    calls = [
        ("GET", "/admin/members", None),
        ("POST", "/admin/members", {"email": "new@example.test", "password": "fixture-secret", "preset": "reader"}),
        ("PUT", f"/admin/members/{uid}/access", {"preset": "editor_operator", "is_admin": True}),
        ("PUT", f"/admin/members/{uid}/certificate-issuer", {"allowed": True}),
    ]
    for method, path, body in calls:
        assert client.request(method, path, json=body).status_code == 401
        assert client.request(method, path, json=body, headers={"Authorization": "Bearer editor"}).status_code == 403
    assert database.member_permissions(uid) == ("editor", True)


def test_admin_applies_separate_access_and_certificate_updates(client, monkeypatch):
    uid = str(uuid4())
    target = str(uuid4())
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {"id": uid, "email": "admin@example.test"})
    monkeypatch.setattr(admin, "postgres", SimpleNamespace(database_url=lambda: "fixture"))
    with database.connect() as connection:
        connection.execute("INSERT INTO members (user_id, role) VALUES (?, 'admin')", (uid,))

    calls = []

    def change_member(actor, user_id, *, access=None, issuer=None):
        calls.append((actor.user_id, str(user_id), access, issuer))
        return admin.DirectoryMember(
            user_id=user_id, email="employee@example.test", preset="editor_operator",
            certificate_issuer=issuer is True,
        )

    monkeypatch.setattr(admin, "change_member", change_member)
    session = {"Authorization": "Bearer admin"}
    path = f"/admin/members/{target}"
    payload = {"preset": "editor_operator"}
    assert client.put(path + "/access", headers=session, json={**payload, "certificate_issuer": True}).status_code == 422
    assert client.put(path + "/access", headers=session, json={**payload, "is_admin": True}).status_code == 422
    assert client.put(path + "/access", headers=session, json={"preset": "admin"}).status_code == 422
    assert not calls
    assigned = client.put(path + "/access", headers=session, json=payload)
    assert assigned.status_code == 200 and not assigned.json()["certificate_issuer"]
    granted = client.put(path + "/certificate-issuer", headers=session, json={"allowed": True})
    assert granted.status_code == 200 and granted.json()["certificate_issuer"]
    assert calls == [(uid, target, admin.MemberAccess(**payload), None), (uid, target, None, True)]

    created_credentials = []

    def create_user(email, password):
        created_credentials.append((email, password))
        return target

    monkeypatch.setattr(admin_auth, "create_user", create_user)
    created = client.post("/admin/members", headers=session, json={
        **payload, "email": "  Employee@Example.test  ", "password": "fixture-only-password",
    })
    assert created.status_code == 201 and created.json()["user_id"] == target
    assert not created.json()["certificate_issuer"] and "fixture-only-password" not in created.text
    assert created_credentials == [("employee@example.test", "fixture-only-password")]
    assert calls[-1][0:2] == (uid, target) and calls[-1][3] is None


def test_dedicated_admin_cannot_use_work_routes(client, monkeypatch):
    uid = str(uuid4())
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setattr(auth, "provider", lambda path, *, token: {"id": uid, "email": "admin@example.test"})
    with database.connect() as connection:
        connection.execute("INSERT INTO members (user_id, role) VALUES (?, 'admin')", (uid,))
    session = {"Authorization": "Bearer admin"}
    result = client.get("/auth/session", headers=session).json()["member"]
    assert result["role"] == "admin" and not result["can_operate_requests"]
    assert "can_issue_certificate" not in result and "can_request_certificate" not in result
    for path in ("/documents", "/requests", "/requests/certificate-clarification-events"):
        assert client.get(path, headers=session).status_code == 403
    assert client.post("/ask", headers=session, json={"question": "Что в базе?"}).status_code == 403
    assert client.post("/requests", headers=session, json={"name": "Admin", "question": "Test request"}).status_code == 403


def test_certificate_override_can_revoke_legacy_issuer(client, monkeypatch):
    uid = str(uuid4())
    monkeypatch.setattr(certificates, "issuer_granted", lambda user_id, override: True if override is None else override)
    with database.connect() as connection:
        connection.execute("INSERT INTO members (user_id, role) VALUES (?, 'operator')", (uid,))
    assert certificates.can_issue(uid, True)
    with database.connect() as connection:
        connection.execute("UPDATE members SET certificate_issuer = FALSE WHERE user_id = ?", (uid,))
    assert not certificates.can_issue(uid, True)
    with database.connect() as connection:
        connection.execute("UPDATE members SET certificate_issuer = TRUE WHERE user_id = ?", (uid,))
    assert not certificates.can_issue(uid, False)
    assert certificates.can_issue(uid, True)
