from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from .config import LOCAL_USER_ID
from .domain import Document, NotFoundError, RequestRecord, RequestReply, RequestStatus, RequestStatusConflict

NEXT_REQUEST_STATUS: dict[RequestStatus, RequestStatus] = {
    "new": "in_progress",
    "in_progress": "resolved",
    "resolved": "in_progress",
}


def new_request(name: str, question: str, document_id: str | None = None, *, user_id: str = LOCAL_USER_ID) -> RequestRecord:
    return RequestRecord(str(uuid4()), name, question, document_id=document_id,
                         created_at=datetime.now(timezone.utc).isoformat(), user_id=user_id)


class DocumentStore(Protocol):
    def list_documents(self) -> list[Document]: ...
    def create_document(self, record: Document, *, owner_id: str | None = None) -> Document: ...
    def get_document(self, document_id: str) -> Document | None: ...
    def update_document(self, document_id: str, changes: dict[str, str]) -> Document | None: ...
    def delete_document(self, document_id: str) -> bool: ...


class RequestStore(Protocol):
    def create_request(self, record: RequestRecord) -> RequestRecord: ...
    def get_request(self, request_id: str, *, user_id: str | None) -> RequestRecord | None: ...
    def add_request_reply(
        self, request_id: str, reply: RequestReply, *, user_id: str | None, operator_id: str | None = None,
    ) -> RequestRecord | None: ...
    def change_request_reply(
        self, request_id: str, reply_id: str, text: str | None, edited_at: str, *, user_id: str | None,
    ) -> RequestRecord | None: ...
    def update_request_status(
        self, request_id: str, expected_status: RequestStatus, status: RequestStatus, *, user_id: str | None,
    ) -> RequestRecord | None: ...


class DocumentService:
    def __init__(self, store: DocumentStore):
        self.store = store

    def list(self) -> list[Document]:
        return self.store.list_documents()

    def create(self, title: str, text: str, source: str, *, owner_id: str | None = None) -> Document:
        record = Document(str(uuid4()), title, text, source)
        return self.store.create_document(record, owner_id=owner_id) if owner_id else self.store.create_document(record)

    def get(self, document_id: str) -> Document:
        record = self.store.get_document(document_id)
        if record is None:
            raise NotFoundError("Document not found.")
        return record

    def update(self, document_id: str, changes: dict[str, str]) -> Document:
        record = self.store.update_document(document_id, changes)
        if record is None:
            raise NotFoundError("Document not found.")
        return record

    def delete(self, document_id: str) -> None:
        if not self.store.delete_document(document_id):
            raise NotFoundError("Document not found.")


class RequestService:
    def __init__(
        self, store: RequestStore, user_id: str = LOCAL_USER_ID, *, role: str | None = None,
        can_operate_requests: bool = False,
    ):
        self.store = store
        self.user_id = user_id
        self.role = role
        self.can_operate_requests = role == "operator" or can_operate_requests
        self.scope = None if self.can_operate_requests else user_id

    def create(
        self, name: str, question: str, document_id: str | None = None
    ) -> RequestRecord:
        return self.store.create_request(new_request(name, question, document_id, user_id=self.user_id))

    def get(self, request_id: str) -> RequestRecord:
        record = self.store.get_request(request_id, user_id=self.scope)
        if record is None:
            raise NotFoundError("Request not found.")
        return record

    def reply(self, request_id: str, reply_id: str, text: str) -> RequestRecord:
        reply = RequestReply(reply_id, text, datetime.now(timezone.utc).isoformat())
        record = self.store.add_request_reply(
            request_id, reply, user_id=self.scope, operator_id=self.user_id if self.can_operate_requests else None,
        )
        if record is None:
            raise NotFoundError("Request not found.")
        return record

    def change_reply(self, request_id: str, reply_id: str, text: str | None) -> RequestRecord:
        record = self.store.change_request_reply(request_id, reply_id, text, datetime.now(timezone.utc).isoformat(), user_id=self.scope)
        if record is None:
            raise NotFoundError("Request reply not found.")
        return record

    def change_status(self, request_id: str, expected_status: RequestStatus, status: RequestStatus) -> RequestRecord:
        if NEXT_REQUEST_STATUS[expected_status] != status:
            self.get(request_id)
            raise RequestStatusConflict("This request status transition is not allowed.")
        record = self.store.update_request_status(request_id, expected_status, status, user_id=self.scope)
        if record is None:
            raise NotFoundError("Request not found.")
        return record
