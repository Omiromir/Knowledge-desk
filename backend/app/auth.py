"""Resolve the authenticated identity from Supabase and the role from our database."""

import os
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException, Request

from . import database

Role = Literal["reader", "editor", "operator"]
COOKIE = "knowledge_desk_access"


@dataclass(frozen=True)
class Member:
    user_id: str
    email: str
    role: Role


def settings() -> tuple[str, str] | None:
    url = os.environ.get("KNOWLEDGE_DESK_SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("KNOWLEDGE_DESK_SUPABASE_PUBLISHABLE_KEY", "").strip()
    if bool(url) != bool(key):
        raise RuntimeError("Configure both the Supabase URL and publishable key.")
    return (url, key) if url else None


def provider(path: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    config = settings()
    if config is None:
        raise HTTPException(404, "Вход через Supabase отключён в локальном режиме.")
    url, key = config
    headers = {"apikey": key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            response = client.request("POST" if payload else "GET", f"{url}/auth/v1/{path}",
                                      headers=headers, json=payload)
        if response.status_code == 429:
            raise HTTPException(429, "Слишком много попыток. Попробуйте позже.")
        if response.status_code in (400, 401, 403):
            raise HTTPException(401, "Проверьте данные для входа или войдите снова.")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Invalid authentication response")
        return value
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Сервис входа временно недоступен.") from None


def member(profile: dict) -> Member:
    try:
        user_id = str(UUID(profile["id"]))
        email = profile["email"]
        if not isinstance(email, str):
            raise ValueError("Missing email")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HTTPException(401, "Войдите снова.") from None
    role = database.member_role(user_id)
    if role not in ("reader", "editor", "operator"):
        raise HTTPException(403, "Для этого аккаунта ещё не назначен доступ к Knowledge Desk.")
    return Member(user_id, email, role)


def current_member(request: Request) -> Member:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Войдите в Knowledge Desk.")
    return member(provider("user", token=token))
