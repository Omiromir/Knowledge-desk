"""Server-side account provisioning through Supabase Auth."""

import os
from uuid import UUID

import httpx
from fastapi import HTTPException

from . import auth


def secret_key() -> str:
    config = auth.settings()
    key = os.getenv("KNOWLEDGE_DESK_SUPABASE_SECRET_KEY", "").strip()
    return key if config and config[0].startswith("https://") and key.startswith("sb_secret_") else ""


def create_user(email: str, password: str) -> UUID:
    key = secret_key()
    config = auth.settings()
    if not config or not key:
        raise HTTPException(503, "Создание аккаунтов не настроено на сервере.")

    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            response = client.post(
                f"{config[0]}/auth/v1/admin/users",
                headers={"apikey": key},
                json={"email": email, "password": password, "email_confirm": True},
            )
        if response.status_code == 429:
            raise HTTPException(429, "Подождите перед созданием следующего аккаунта.")
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Invalid Auth response")
        if response.status_code == 409 or value.get("error_code") in {"email_exists", "user_already_exists"}:
            raise HTTPException(409, "Аккаунт с этой почтой уже есть. Выберите его в списке пользователей.")
        if response.status_code in {400, 422}:
            raise HTTPException(400, "Supabase не принял почту или пароль. Проверьте поля.")
        response.raise_for_status()
        user_id = UUID(value["id"])
        if value.get("email", "").lower() != email:
            raise ValueError("Unexpected Auth account")
        return user_id
    except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError):
        raise HTTPException(
            503, {"code": "admin_creation_unconfirmed", "message": "Создание аккаунта не подтверждено."},
        ) from None
