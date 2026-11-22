from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.app import auth, database


@pytest.fixture
def members(client, monkeypatch):
    roles = {"reader": "reader", "other": "reader", "operator": "operator", "admin": "admin"}
    people = {name: {"id": str(uuid4()), "email": f"{name}@example.test"} for name in roles}
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO members (user_id, role) VALUES (?, ?)",
            [(person["id"], roles[name]) for name, person in people.items()],
        )
    writes = []

    def provider(path, *, token=None, payload=None, method=None):
        if path == "user" and method is None:
            return people[token]
        if path == "token?grant_type=password":
            if payload == {"email": people["reader"]["email"], "password": "current fixture password"}:
                return {"access_token": "fresh-reader-token", "user": people["reader"]}
            raise HTTPException(401)
        assert path == "user" and method == "PUT"
        writes.append((token, payload))
        return people["reader"]

    monkeypatch.setattr(auth, "provider", provider)
    return client, writes


def test_personal_profile_is_scoped_to_verified_member(members):
    client, _ = members
    reader = {"Authorization": "Bearer reader"}
    admin = {"Authorization": "Bearer admin"}
    assert client.get("/profile").status_code == 401
    assert client.patch("/profile", headers=reader, json={"display_name": "Имя", "role": "admin"}).status_code == 422
    response = client.patch("/profile", headers=reader, json={"display_name": "  Нина Соколова  "})
    assert response.status_code == 200
    assert response.json() == {"display_name": "Нина Соколова", "avatar_version": None}
    assert client.get("/profile", headers=reader).json() == response.json()
    assert client.get("/profile", headers=admin).json() == {"display_name": "", "avatar_version": None}

    assert client.patch("/profile", headers=admin, json={"display_name": "Админ"}).status_code == 200
    assert client.get("/profile", headers=admin).json()["display_name"] == "Админ"
    assert client.get("/profile", headers=reader).json()["display_name"] == "Нина Соколова"


def test_avatar_is_normalized_private_and_independent_of_name(members):
    client, _ = members
    reader = {"Authorization": "Bearer reader"}
    admin = {"Authorization": "Bearer admin"}
    client.patch("/profile", headers=reader, json={"display_name": "Нина"})
    image = BytesIO()
    Image.new("RGB", (320, 240), "#624eaa").save(image, format="PNG")
    assert client.put("/profile/avatar", headers=reader, content=b"<svg/>").status_code == 415
    assert client.put("/profile/avatar", headers=reader, content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    uploaded = client.put("/profile/avatar", headers=reader, content=image.getvalue())
    assert uploaded.status_code == 200
    assert uploaded.json()["display_name"] == "Нина"
    assert uploaded.json()["avatar_version"]

    photo = client.get("/profile/avatar", headers=reader)
    assert photo.headers["content-type"] == "image/webp"
    assert photo.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(photo.content)) as result:
        assert result.format == "WEBP" and result.size == (192, 192)
    assert client.get("/profile/avatar", headers=admin).status_code == 404
    assert client.get("/profile/avatar").status_code == 401

    removed = client.delete("/profile/avatar", headers=reader)
    assert removed.status_code == 200
    assert removed.json() == {"display_name": "Нина", "avatar_version": None}
    assert client.get("/profile/avatar", headers=reader).status_code == 404


def test_password_update_rechecks_current_password_and_ends_browser_session(members):
    client, writes = members
    client.cookies.set(auth.COOKIE, "reader", domain="testserver.local", path="/")
    endpoint = "/profile/password"
    old = "current fixture password"
    new = "new fixture password"
    assert client.put(endpoint, json={"current_password": old, "new_password": "short"}).status_code == 422
    assert client.put(endpoint, json={"current_password": old, "new_password": "я" * 37}).status_code == 400
    rejected = client.put(endpoint, json={"current_password": "wrong password", "new_password": new})
    assert rejected.status_code == 400
    assert not writes and "set-cookie" not in rejected.headers
    updated = client.put(endpoint, json={"current_password": old, "new_password": new})
    assert updated.status_code == 204
    assert "Max-Age=0" in updated.headers["set-cookie"]
    assert writes == [("fresh-reader-token", {"password": new, "current_password": old})]


def test_thread_profiles_follow_the_real_participants_and_avatar_access(members, monkeypatch):
    client, _ = members
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "test-key")
    reader, other, operator = ({"Authorization": f"Bearer {name}"} for name in ("reader", "other", "operator"))
    assert client.patch("/profile", headers=reader, json={"display_name": "Нина"}).status_code == 200
    assert client.patch("/profile", headers=operator, json={"display_name": "Роман"}).status_code == 200
    image = BytesIO()
    Image.new("RGB", (50, 50), "#624eaa").save(image, format="PNG")
    photo = client.put("/profile/avatar", headers=operator, content=image.getvalue())
    assert photo.status_code == 200
    version = photo.json()["avatar_version"]

    created = client.post("/requests", headers=reader, json={"name": "Имя в форме", "question": "Вопрос"})
    assert created.status_code == 201
    path = "/requests/" + created.json()["id"]
    reply_id = str(uuid4())
    sent = client.post(path + "/replies", headers=operator, json={"id": reply_id, "text": "Ответ по заявке"})
    assert sent.status_code == 200
    assert sent.json()["replies"][0]["author"] == {"display_name": "Роман", "avatar_version": version, "is_self": True}
    assert "author_id" not in sent.json()["replies"][0]
    assert client.patch("/profile", headers=operator, json={"display_name": "Новое имя"}).status_code == 200
    detail = client.get(path, headers=reader)
    assert detail.status_code == 200
    assert detail.json()["name"] == "Имя в форме"
    assert detail.json()["author"] == {"display_name": "Нина", "is_self": True}
    assert detail.json()["replies"][0]["author"] == {"display_name": "Новое имя", "avatar_version": version}
    assert "author" not in client.get("/requests", headers=reader).json()[0]
    assert "author" not in client.get("/requests", headers=reader).json()[0]["replies"][0]

    avatar_path = f"{path}/avatars/{version}"
    shared = client.get(avatar_path, headers=reader)
    assert shared.status_code == 200 and shared.headers["cache-control"] == "private, no-store"
    assert shared.content == client.get("/profile/avatar", headers=operator).content
    assert client.get(avatar_path, headers=other).status_code == 404
    assert client.get(avatar_path).status_code == 401
    unrelated = client.post("/requests", headers=other, json={"name": "Другая заявка", "question": "Вопрос"})
    assert unrelated.status_code == 201
    assert client.get(f"/requests/{unrelated.json()['id']}/avatars/{version}", headers=other).status_code == 404
    assert client.delete("/profile/avatar", headers=operator).status_code == 200
    assert client.get(avatar_path, headers=reader).status_code == 404
