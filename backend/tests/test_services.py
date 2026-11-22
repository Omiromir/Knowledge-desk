from unittest.mock import Mock
from uuid import UUID

import pytest

from backend.app.domain import Document, NotFoundError
from backend.app.services import DocumentService, DocumentStore, RequestService, RequestStore


def test_document_creation_assigns_id_before_storage():
    store = Mock(spec=DocumentStore)
    store.create_document.side_effect = lambda record: record
    record = DocumentService(store).create("Title", "Text", "Source")
    assert UUID(record.id).version == 4
    assert (record.title, record.text, record.source) == ("Title", "Text", "Source")
    store.create_document.assert_called_once_with(record)


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_missing_document_is_an_application_error(operation):
    store = Mock(spec=DocumentStore)
    store.get_document.return_value = None
    store.update_document.return_value = None
    store.delete_document.return_value = False
    service = DocumentService(store)
    with pytest.raises(NotFoundError, match="Document not found"):
        if operation == "update":
            service.update("missing", {"title": "New"})
        else:
            getattr(service, operation)("missing")


def test_request_creation_assigns_id_and_initial_status():
    store = Mock(spec=RequestStore)
    store.create_request.side_effect = lambda record: record
    record = RequestService(store).create("Reader", "Question")
    assert UUID(record.id).version == 4
    assert (record.name, record.question, record.status) == ("Reader", "Question", "new")
    store.create_request.assert_called_once_with(record)


def test_missing_request_is_an_application_error():
    store = Mock(spec=RequestStore)
    store.get_request.return_value = None
    with pytest.raises(NotFoundError, match="Request not found"):
        RequestService(store).get("missing")


def test_document_update_passes_only_requested_changes():
    store = Mock(spec=DocumentStore)
    record = Document("id", "New", "Original text", "Original source")
    store.update_document.return_value = record
    assert DocumentService(store).update("id", {"title": "New"}) == record
    store.update_document.assert_called_once_with("id", {"title": "New"})
