from unittest.mock import Mock

import pytest
from redis.exceptions import TimeoutError

from backend.app import postgres, rag, uploads, vector_index
from backend.app.summarizer import SummaryError


def test_cloud_sources_and_search_use_the_database_with_the_same_user(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DESK_REDIS_URL", raising=False)
    monkeypatch.setattr(postgres, "database_url", lambda: "postgresql://example.test")
    local_index = Mock(side_effect=AssertionError("Chroma must not be opened"))
    monkeypatch.setattr(rag, "manifest_collection", local_index)
    monkeypatch.setattr(uploads, "collection", local_index)
    embed = Mock(return_value=[[1.0]])
    monkeypatch.setattr(rag, "embed", embed)
    search = Mock(return_value=[{"chunk_id": "old-id"}])
    find = Mock(return_value={"chunk_id": "old-id", "doc_id": "old-doc", "title": "Document",
                              "source": "doc.md", "version_date": "2026-11-20", "text": "quote",
                              "start": 7, "end": 12, "document_text": "Before quote after"})
    monkeypatch.setattr(vector_index, "search", search)
    monkeypatch.setattr(vector_index, "source", find)

    assert rag.retrieve_question("Where?", user_id="actor") == [{"chunk_id": "old-id"}]
    search.assert_called_once_with([1.0], "actor")
    cite = rag.get_source("old-id", user_id="actor")
    assert (cite.chunk_id, cite.before, cite.text, cite.after) == ("old-id", "Before ", "quote", " after")
    find.assert_called_once_with("old-id", "actor")
    assert rag.retrieve_question("Where?") == []
    with pytest.raises(SummaryError, match="not found"):
        rag.get_source("old-id")
    local_index.assert_not_called()


def test_cloud_source_rejects_a_chunk_that_does_not_match_current_text(monkeypatch):
    monkeypatch.setattr(postgres, "database_url", lambda: "postgresql://example.test")
    monkeypatch.setattr(vector_index, "source", lambda *_: {"document_text": "Changed", "start": 0, "end": 5, "text": "quote"})
    with pytest.raises(SummaryError, match="not found"):
        rag.get_source("old-id", user_id="actor")


def test_cached_vector_does_not_cache_document_access(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DESK_REDIS_URL", "redis://cache.test")
    monkeypatch.setattr(postgres, "database_url", lambda: "postgresql://example.test")
    entries = {}
    cache = Mock()
    cache.get.side_effect = entries.get
    cache.set.side_effect = lambda key, payload, *, ex: entries.update({key: payload})
    monkeypatch.setattr(rag, "query_cache", lambda _: cache)
    embed = Mock(return_value=[[0.5] * rag.DIMENSION])
    monkeypatch.setattr(rag, "embed", embed)
    search = Mock(side_effect=[[{"chunk_id": "private"}], [], [], []])
    monkeypatch.setattr(vector_index, "search", search)

    assert rag.retrieve_question("Private question?", user_id="owner") == [{"chunk_id": "private"}]
    assert rag.retrieve_question("Private question?", user_id="owner") == []
    assert embed.call_count == 1
    assert rag.retrieve_question("Private question?", user_id="reader") == []
    assert embed.call_count == 2
    assert len(entries) == 2
    assert all("Private question?" not in key for key in entries)
    assert all(call.kwargs == {"ex": 600} for call in cache.set.call_args_list)

    cache.get.side_effect = TimeoutError
    assert rag.retrieve_question("Private question?", user_id="owner") == []
    assert embed.call_count == 3
    assert [call.args[1] for call in search.call_args_list] == ["owner", "owner", "reader", "owner"]
