from typing import Annotated, Literal

from pydantic import UUID4, BaseModel, StringConstraints, field_validator, model_validator

from .domain import RequestReply, RequestStatus


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


class RequestRecord(RequestCreate):
    id: str
    status: RequestStatus = "new"
    replies: tuple[RequestReply, ...] | None = None
    created_at: str | None = None


class RequestStatusUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: RequestStatus
    expected_status: RequestStatus


class RequestReplyText(BaseModel):
    model_config = {"extra": "forbid"}
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class RequestReplyCreate(RequestReplyText):
    id: UUID4


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
