"""Exercise real Chroma writes in an isolated directory with deterministic vectors."""

import json
import pytest

import ingest


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(ingest, "DEMO_CORPUS", False)
    monkeypatch.setattr(ingest, "embed", lambda texts, _: [[1.0] + [0.0] * 383 for _ in texts])
    return tmp_path


def entry(filename, doc_id="policy"):
    return {"doc_id": doc_id, "title": "Правило", "source": filename,
            "path": filename, "version_date": "2026-11-20"}


def write_manifest(root, rows):
    (root / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(("case", "expected_status", "decision"), [
    ("empty", "skipped", "skipped"),
    ("whitespace", "skipped", "skipped"),
    ("duplicate", "complete", "skipped"),
    ("broken-manifest", "rejected", "rejected"),
    ("missing-source", "rejected", "rejected"),
])
def test_import_decisions_do_not_pollute_existing_index(corpus, case, expected_status, decision):
    (corpus / "valid.txt").write_text("Обработка занимает два дня.", encoding="utf-8")
    write_manifest(corpus, [entry("valid.txt")])
    index_path = corpus / "index.chroma"
    first = ingest.ingest(index_path)
    assert first["status"] == "complete" and first["chunks"] == 1
    original = ingest.open_collection(index_path).get(include=[])["ids"]
    problem = f"{case}.txt"
    (corpus / problem).write_text({"empty": "", "whitespace": " \n\t ",
                                 "duplicate": "Обработка занимает два дня."}.get(case, "Текст документа."), encoding="utf-8")
    rows = [entry(problem, "problem")]
    if case == "duplicate":
        rows.insert(0, entry("valid.txt"))
    if case == "missing-source":
        rows[0].pop("source")
        rows.insert(0, entry("valid.txt"))
    write_manifest(corpus, rows)
    if case == "broken-manifest":
        (corpus / "manifest.json").write_text('{"broken":', encoding="utf-8")

    report = ingest.ingest(index_path)
    assert report["status"] == expected_status
    assert report["before"] == report["after"] == report["chunks"] == 1
    assert report["upserted"] == report["removed"] == 0
    assert any(row["decision"] == decision and row["file"] == (
        "manifest.json" if case == "broken-manifest" else problem) for row in report["files"])
    assert ingest.open_collection(index_path).get(include=[])["ids"] == original
    if case == "duplicate":
        assert list(ingest.load_corpus()) == ["policy"]


def test_repeat_import_preserves_fragment_ids(corpus):
    (corpus / "valid.txt").write_text("Правило обработки. " * 50, encoding="utf-8")
    write_manifest(corpus, [entry("valid.txt")])
    index_path = corpus / "index.chroma"
    first = ingest.ingest(index_path)
    ids = ingest.open_collection(index_path).get(include=[])["ids"]
    second = ingest.ingest(index_path)
    assert first["chunks"] > 1
    assert second["chunks"] == first["chunks"]
    assert second["upserted"] == second["removed"] == 0
    assert sorted(ids) == sorted(ingest.open_collection(index_path).get(include=[])["ids"])


def test_valid_documents_remain_readable_with_skipped_empty_file(corpus):
    (corpus / "valid.txt").write_text("Обработка занимает два дня.", encoding="utf-8")
    (corpus / "empty.txt").write_text("  \n", encoding="utf-8")
    write_manifest(corpus, [entry("valid.txt"), entry("empty.txt", "empty")])
    report = ingest.ingest(corpus / "index.chroma")
    assert report["status"] == "complete" and report["chunks"] == 1
    assert list(ingest.load_corpus()) == ["policy"]
