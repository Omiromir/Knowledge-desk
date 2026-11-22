"""Fill and release one kind of employment certificate using a test register."""

import os
from dataclasses import replace
from datetime import date, datetime, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

from docx import Document
from fastapi import HTTPException
from pydantic import UUID4, BaseModel, StringConstraints
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from . import certificate_notifications, database, postgres
from .certificate_updates import CHANNEL as CERTIFICATE_UPDATE_CHANNEL
from .certificate_updates import card_topic, owner_topic
from .domain import NotFoundError, RequestRecord
from .services import new_request

KIND = "employment_certificate"
TITLE = "Справка с места работы"
MAX_PDF_BYTES = 5 * 1024 * 1024
TEMPLATE = Path(__file__).with_name("templates") / "employment-certificate.docx"

SINGLE_LINE = r"^[^\x00-\x1f\ufffe\uffff]*$"
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=SINGLE_LINE)]


class Employee(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: ShortText
    employer: ShortText
    position: ShortText
    employed_from: date


class Registry(BaseModel):
    model_config = {"extra": "forbid"}
    issuer_ids: set[UUID]
    employees: dict[UUID, Employee]


class CertificateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160, pattern=SINGLE_LINE)] = ""


class ClarificationText(BaseModel):
    model_config = {"extra": "forbid"}
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=r"^[^\x00]*$")]


class ClarificationCreate(ClarificationText):
    id: UUID4


@lru_cache(maxsize=1)
def read_registry(path: str) -> Registry:
    try:
        return Registry.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise HTTPException(503, "Тестовый реестр справок недоступен.") from None


def registry() -> Registry | None:
    path = os.environ.get("KNOWLEDGE_DESK_TEST_CERTIFICATE_REGISTRY", "").strip()
    return read_registry(path) if path else None


def can_request(user_id: str) -> bool:
    data = registry()
    return data is not None and UUID(user_id) in data.employees


def issuer_granted(user_id: str, override: bool | None) -> bool:
    if override is not None:
        return override
    data = registry()
    return data is not None and UUID(user_id) in data.issuer_ids


def can_issue(user_id: str, can_operate_requests: bool) -> bool:
    return can_operate_requests and issuer_granted(user_id, database.member_certificate_issuer(user_id))


def render_draft(employee: Employee, purpose: str) -> bytes:
    document = Document(TEMPLATE)
    values = {
        "[ФИО]": employee.full_name,
        "[Организация]": employee.employer,
        "[Должность]": employee.position,
        "[Дата начала работы]": employee.employed_from.strftime("%d.%m.%Y"),
        "[Назначение]": purpose or "По месту требования",
    }
    found = set()
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            if run.text in values:
                found.add(run.text)
                run.text = values[run.text]
    if found != values.keys():
        raise RuntimeError("The employment certificate template is incomplete.")
    result = BytesIO()
    document.save(result)
    return result.getvalue()


def set_actor(connection, user_id: str, *, issuer: bool = False) -> None:
    if postgres.database_url():
        connection.execute(
            """SELECT set_config('knowledge_desk.certificate_actor', ?, true),
                      set_config('knowledge_desk.certificate_issuer', ?, true)""",
            (user_id, "1" if issuer else "0"),
        )


def create(user_id: str, purpose: str, *, email: str | None = None) -> RequestRecord:
    data = registry()
    employee = data.employees.get(UUID(user_id)) if data else None
    if employee is None:
        raise HTTPException(403, "Для вашего аккаунта нет записи в тестовом реестре.")
    draft = render_draft(employee, purpose)
    record = replace(new_request(employee.full_name, TITLE, user_id=user_id), kind=KIND)
    with database.connect() as connection:
        set_actor(connection, user_id)
        database.insert_request(connection, record)
        connection.execute("UPDATE requests SET kind = ? WHERE id = ?", (KIND, record.id))
        connection.execute("INSERT INTO request_certificates (request_id, draft) VALUES (?, ?)", (record.id, draft))
        if email:
            certificate_notifications.prepare(connection, record.id, email)
    return record


def download(request_id: str, user_id: str, *, draft: bool, issuer: bool) -> bytes:
    with database.connect() as connection:
        set_actor(connection, user_id, issuer=issuer)
        if draft:
            row = connection.execute("SELECT draft FROM request_certificates WHERE request_id = ?", (request_id,)).fetchone()
            content = row["draft"] if row else None
        else:
            row = connection.execute(
                "SELECT signed_pdf FROM request_certificates WHERE request_id = ?", (request_id,),
            ).fetchone()
            content = row["signed_pdf"] if row else None
    if content is None:
        raise NotFoundError("Certificate file not found.")
    return content


def clarifications(request_id: str, user_id: str, *, issuer: bool) -> list[dict]:
    with database.connect() as connection:
        set_actor(connection, user_id, issuer=issuer)
        rows = connection.execute(
            """SELECT id, question, asked_by, asked_at, answer, answered_at
               FROM certificate_clarifications WHERE request_id = ? ORDER BY asked_at, id""",
            (request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def waiting_requests(user_id: str, *, issuer: bool) -> set[str]:
    with database.connect() as connection:
        set_actor(connection, user_id, issuer=issuer)
        rows = connection.execute(
            """SELECT c.request_id FROM certificate_clarifications AS c
               JOIN requests AS r ON r.id = c.request_id
               WHERE c.answered_at IS NULL AND r.status <> 'resolved' AND (? OR r.user_id = ?)""",
            (issuer, user_id),
        ).fetchall()
    return {row["request_id"] for row in rows}


def lock_request(connection, request_id: str) -> str:
    row = connection.execute(
        "SELECT status FROM requests WHERE id = ? AND kind = ?" + postgres.lock_row(), (request_id, KIND),
    ).fetchone()
    if row is None:
        raise NotFoundError("Certificate request not found.")
    return row["status"]


def is_waiting(connection, request_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM certificate_clarifications WHERE request_id = ? AND answered_at IS NULL", (request_id,),
    ).fetchone() is not None


def ask_clarification(request_id: str, issuer_id: str, clarification_id: str, question: str, *, owner_id: str) -> bool:
    with database.connect(immediate=True) as connection:
        set_actor(connection, issuer_id, issuer=True)
        status = lock_request(connection, request_id)
        previous = connection.execute(
            "SELECT question, asked_by FROM certificate_clarifications WHERE request_id = ? AND id = ?",
            (request_id, clarification_id),
        ).fetchone()
        if previous is not None:
            if previous["question"] == question and previous["asked_by"] == issuer_id:
                return False
            raise HTTPException(409, "Уточнение с таким ID уже сохранено.")
        if status == "resolved":
            raise HTTPException(409, "Справка уже выдана.")
        if is_waiting(connection, request_id):
            raise HTTPException(409, "Заявка уже ждёт ответа сотрудника. Обновите карточку.")
        connection.execute(
            """INSERT INTO certificate_clarifications (request_id, id, question, asked_by, asked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (request_id, clarification_id, question, issuer_id, datetime.now(timezone.utc).isoformat()),
        )
        if postgres.database_url():
            connection.execute(
                "SELECT pg_notify(?, ?), pg_notify(?, ?)",
                (CERTIFICATE_UPDATE_CHANNEL, card_topic(request_id), CERTIFICATE_UPDATE_CHANNEL, owner_topic(owner_id)),
            )
    return True


def answer_clarification(request_id: str, employee_id: str, clarification_id: str, answer: str) -> bool:
    with database.connect(immediate=True) as connection:
        set_actor(connection, employee_id)
        status = lock_request(connection, request_id)
        previous = connection.execute(
            "SELECT answer FROM certificate_clarifications WHERE request_id = ? AND id = ?",
            (request_id, clarification_id),
        ).fetchone()
        if previous is None:
            raise NotFoundError("Clarification not found.")
        if previous["answer"] == answer:
            return False
        if status == "resolved" or previous["answer"] is not None:
            raise HTTPException(409, "Это уточнение уже закрыто. Обновите карточку.")
        updated = connection.execute(
            """UPDATE certificate_clarifications SET answer = ?, answered_at = ? WHERE request_id = ? AND id = ?""",
            (answer, datetime.now(timezone.utc).isoformat(), request_id, clarification_id),
        )
        if updated.rowcount != 1:
            raise NotFoundError("Clarification not found.")
        connection.execute("UPDATE requests SET status = 'in_progress' WHERE id = ?", (request_id,))
        if postgres.database_url():
            connection.execute(
                "SELECT pg_notify(?, ''), pg_notify(?, ?)",
                (CERTIFICATE_UPDATE_CHANNEL, CERTIFICATE_UPDATE_CHANNEL, owner_topic(employee_id)),
            )
    return True


def issue(request_id: str, user_id: str, content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise HTTPException(422, "Прикрепите подписанную справку в PDF.")
    try:
        pdf = PdfReader(BytesIO(content), strict=True)
        if pdf.is_encrypted or not pdf.pages:
            raise ValueError("Unreadable PDF")
    except (PyPdfError, ValueError, OSError, RecursionError):
        raise HTTPException(422, "Не удалось прочитать PDF. Выберите исправный файл без пароля.") from None

    # shortcut: bounded pilot files share the database transaction, move to private object storage if volume grows.
    with database.connect(immediate=True) as connection:
        set_actor(connection, user_id, issuer=True)
        lock_request(connection, request_id)
        if is_waiting(connection, request_id):
            raise HTTPException(409, "Дождитесь ответа сотрудника перед выдачей справки.")
        pending = connection.execute(
            """UPDATE request_certificates SET signed_pdf = ?, issued_by = ?, issued_at = ?
               WHERE request_id = ? AND issued_at IS NULL RETURNING request_id""",
            (content, user_id, datetime.now(timezone.utc).isoformat(), request_id),
        ).fetchone()
        if pending:
            connection.execute("UPDATE requests SET status = 'resolved' WHERE id = ?", (request_id,))
            certificate_notifications.queue(connection, request_id)
            return
        existing = connection.execute(
            "SELECT signed_pdf FROM request_certificates WHERE request_id = ?", (request_id,),
        ).fetchone()
        if existing is None:
            raise NotFoundError("Certificate request not found.")
        if existing["signed_pdf"] != content:
            raise HTTPException(409, "Справка уже выдана. Обновите заявку.")
