import json
from importlib import import_module

import pytest

from backend.rag_index import ingest, retrieve


def test_legacy_imports_share_the_index_implementation():
    assert import_module("ingest") is ingest
    assert import_module("retrieve") is retrieve


def test_bundled_corpus_resolves_a_published_chunk():
    corpus = ingest.load_corpus()
    chunk_id, text, metadata = next(ingest.chunk_document(corpus["python-testing"]))

    assert chunk_id == "python-testing-000-8a24c1de5e3976821f7a"
    assert retrieve.source_record(chunk_id, text, metadata, corpus)["source"] == "data/documents/python-testing.md"


def test_chunk_size_changes_ids_without_changing_the_default():
    document = ingest.Document("test", "Test", "test.md", "2026-11-08", "word " * 149 + "end")
    by_size = {size: list(ingest.chunk_document(document, chunk_size=size)) for size in ingest.CHUNK_SIZES}
    assert list(ingest.chunk_document(document)) == by_size[400]
    assert len(by_size[300]) > len(by_size[600])
    all_ids = []
    for size, chunks in by_size.items():
        assert chunks == list(ingest.chunk_document(document, chunk_size=size))
        assert all(text == document.text[meta["start"]:meta["end"]] for _, text, meta in chunks)
        all_ids.extend(chunk_id for chunk_id, _, _ in chunks)
    assert len(all_ids) == len(set(all_ids))


def test_external_source_is_distinct_from_the_local_text(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    document = tmp_path / "policy.txt"
    document.write_text("Section 4. Give 30 days' written notice.", encoding="utf-8-sig")
    entry = {
        "doc_id": "policy", "title": "Notice policy", "version_date": "2026-10-01",
        "path": "policy.txt", "source": "https://example.invalid/policies/notice",
    }
    manifest.write_text(json.dumps([entry]), encoding="utf-8-sig")
    monkeypatch.setattr(ingest, "MANIFEST", manifest)
    monkeypatch.setattr(ingest, "DEMO_CORPUS", False)

    loaded = ingest.load_corpus()["policy"]
    _, text, metadata = next(ingest.chunk_document(loaded))
    assert loaded.path == document
    assert text == "Section 4. Give 30 days' written notice."
    assert metadata["source"] == entry["source"]
    assert metadata["version_date"] == entry["version_date"]

    manifest.write_text(json.dumps([{**entry, "path": "../outside.txt"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid or repeated document path"):
        ingest.load_corpus()
