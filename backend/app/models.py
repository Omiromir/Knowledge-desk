from typing import Annotated, Literal

from pydantic import UUID4, BaseModel, StringConstraints, field_validator, model_validator

from .domain import RequestStatus


NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class DocumentCreate(BaseModel):
    title: NonEmptyString
    text: NonEmptyString
    source: NonEmptyString


class DocumentPatch(BaseModel):
    model_config = {"extra": "forbid"}

    title: NonEmptyString | None = None
    text: NonEmptyString | None = None
    source: NonEmptyString | None = None

    @model_validator(mode="before")
    @classmethod
    def require_nonempty_update(cls, values: object) -> object:
        if not isinstance(values, dict) or not values:
            raise ValueError("Provide at least one document field to update.")
        if any(value is None for value in values.values()):
            raise ValueError("Document fields cannot be null.")
        return values


class Document(DocumentCreate):
    id: str


class RequestCreate(BaseModel):
    name: NonEmptyString
    question: NonEmptyString
    document_id: NonEmptyString | None = None


CertificateEmailStatus = Literal["pending", "accepted", "delayed", "delivered", "not_delivered", "unknown"]


class MessageAuthor(BaseModel):
    display_name: str | None = None
    avatar_version: str | None = None
    is_self: bool | None = None


class CertificateClarification(BaseModel):
    id: UUID4
    question: str
    asked_at: str
    answer: str | None
    answered_at: str | None
    question_author: MessageAuthor | None = None
    answer_author: MessageAuthor | None = None


class RequestReplyRecord(BaseModel):
    id: str
    text: str
    created_at: str
    edited_at: str | None = None
    author: MessageAuthor | None = None


class RequestRecord(RequestCreate):
    id: str
    status: RequestStatus = "new"
    author: MessageAuthor | None = None
    replies: tuple[RequestReplyRecord, ...] | None = None
    created_at: str | None = None
    kind: Literal["employment_certificate"] | None = None
    certificate_downloadable: bool | None = None
    certificate_owned: bool | None = None
    certificate_email_status: CertificateEmailStatus | None = None
    certificate_awaiting_reply: bool | None = None
    certificate_clarifications: list[CertificateClarification] | None = None
    certificate_can_answer: bool | None = None


class RequestStatusUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: RequestStatus
    expected_status: RequestStatus


class RequestReplyText(BaseModel):
    model_config = {"extra": "forbid"}
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class RequestReplyCreate(RequestReplyText):
    id: UUID4


class RequestReplyDraft(BaseModel):
    revision: UUID4
    text: Annotated[str, StringConstraints(max_length=10000)]


class RequestReplyDraftWrite(RequestReplyDraft):
    model_config = {"extra": "forbid"}
    expected_revision: UUID4 | None


MAX_SUMMARY_INPUT = 4000


class SummaryInput(BaseModel):
    model_config = {"extra": "forbid"}

    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_SUMMARY_INPUT)]

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question text cannot be blank.")
        return value


class SummaryText(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    summary: str
    needs_human: bool

    @field_validator("summary")
    @classmethod
    def short_summary(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 360:
            raise ValueError("Summary must contain between 1 and 360 characters.")
        return value


class RequestSummary(SummaryText):
    request_type: Literal["course_question", "consultation", "out_of_scope"]


class GeneralRequestSummary(SummaryText):
    request_type: Literal["information_request", "consultation", "out_of_scope"]
