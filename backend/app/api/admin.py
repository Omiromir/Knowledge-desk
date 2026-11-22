from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import Error as PostgresError
from pydantic import BaseModel, Field, SecretStr, StrictBool, StringConstraints, field_validator

from .. import admin_auth, auth, certificates, database, postgres
from ..rate_limit import limit_expensive

router = APIRouter(prefix="/admin/members", tags=["administration"])
Preset = Literal["reader", "editor", "operator", "editor_operator"]


class MemberAccess(BaseModel):
    model_config = {"extra": "forbid"}

    preset: Preset


class AccountCreate(MemberAccess):
    email: Annotated[
        str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    ]
    password: Annotated[SecretStr, Field(min_length=12, max_length=72)]

    @field_validator("password")
    @classmethod
    def limit_password_bytes(cls, password: SecretStr) -> SecretStr:
        if len(password.get_secret_value().encode("utf-8")) > 72:
            raise ValueError("Password exceeds 72 UTF-8 bytes")
        return password


class CertificateAccess(BaseModel):
    model_config = {"extra": "forbid"}

    allowed: StrictBool


class DirectoryMember(BaseModel):
    user_id: UUID
    email: str
    preset: Preset | None
    certificate_issuer: bool


class Directory(BaseModel):
    members: list[DirectoryMember]
    account_creation_enabled: bool


def require_admin(request: Request) -> auth.Member:
    if auth.settings() is None:
        raise HTTPException(401, "Войдите как администратор.")
    member = auth.current_member(request)
    if member.role != "admin":
        raise HTTPException(403, "Доступно только администратору.")
    if not postgres.database_url():
        raise HTTPException(503, "Для управления пользователями требуется Supabase.")
    return member


Admin = Annotated[auth.Member, Depends(require_admin)]


def directory_member(row) -> DirectoryMember:
    preset = "editor_operator" if row["role"] == "editor" and row["can_operate_requests"] else row["role"]
    return DirectoryMember(
        user_id=row["user_id"], email=row["email"], preset=preset,
        certificate_issuer=certificates.issuer_granted(row["user_id"], row["certificate_issuer"]),
    )


def directory_rows(actor_id: str):
    try:
        with database.connect() as connection:
            return connection.execute("SELECT * FROM admin_member_directory(?)", (actor_id,)).fetchall()
    except PostgresError as error:
        raise database_error(error) from None


def database_error(error: PostgresError) -> HTTPException:
    responses = {
        "42501": (403, "Доступно только администратору."),
        "P0002": (404, "Пользователь не найден."),
        "KD001": (409, "Учётная запись администратора управляется в Supabase."),
        "KD002": (409, "Сначала выдайте пользователю доступ к очереди заявок."),
    }
    status, detail = responses.get(error.sqlstate, (503, "Не удалось обновить права. Попробуйте ещё раз."))
    return HTTPException(status, detail)


def change_member(
    admin: auth.Member, user_id: UUID, *, access: MemberAccess | None = None, issuer: bool | None = None,
) -> DirectoryMember:
    try:
        with database.connect() as connection:
            connection.execute(
                "SELECT admin_manage_member(?, ?, ?, ?)",
                (admin.user_id, user_id, access.preset if access else None, issuer),
            )
            row = connection.execute(
                "SELECT * FROM admin_member_directory(?, ?)", (admin.user_id, user_id),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "Пользователь не найден.")
            return directory_member(row)
    except PostgresError as error:
        raise database_error(error) from None


@router.get("", response_model=Directory)
def list_members(admin: Admin):
    return Directory(
        members=[directory_member(row) for row in directory_rows(admin.user_id)],
        account_creation_enabled=bool(admin_auth.secret_key()),
    )


@router.post("", response_model=DirectoryMember, status_code=201)
def create_account(payload: AccountCreate, request: Request, admin: Admin):
    limit_expensive(request, admin.user_id)
    user_id = admin_auth.create_user(payload.email, payload.password.get_secret_value())
    try:
        return change_member(admin, user_id, access=payload)
    except HTTPException as error:
        if error.status_code == 403:
            raise
        raise HTTPException(
            503, {"code": "admin_account_without_access", "message": "Аккаунт создан без доступа."},
        ) from None


@router.put("/{user_id}/access", response_model=DirectoryMember)
def update_access(user_id: UUID, access: MemberAccess, admin: Admin):
    return change_member(admin, user_id, access=access)


@router.put("/{user_id}/certificate-issuer", response_model=DirectoryMember)
def update_issuer(user_id: UUID, access: CertificateAccess, admin: Admin):
    return change_member(admin, user_id, issuer=access.allowed)
