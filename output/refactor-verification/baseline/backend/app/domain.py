from dataclasses import dataclass
from typing import Literal

from .config import LOCAL_USER_ID

RequestStatus = Literal["new", "in_progress", "resolved"]


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str
    source: str


@dataclass(frozen=True)
class RequestReply:
    id: str
    text: str
    created_at: str
    edited_at: str | None = None
    author_id: str | None = None


@dataclass(frozen=True)
class RequestRecord:
    id: str
    name: str
    question: str
    status: RequestStatus = "new"
    document_id: str | None = None
    replies: tuple[RequestReply, ...] | None = None
    created_at: str | None = None
    user_id: str = LOCAL_USER_ID
    kind: Literal["employment_certificate"] | None = None


class NotFoundError(Exception):
    pass


class RequestStatusConflict(Exception):
    pass


class RequestReplyConflict(Exception):
    pass


class RequestReplyDraftConflict(Exception):
    pass
