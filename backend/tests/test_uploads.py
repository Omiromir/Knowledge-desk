import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile

import pytest
from PIL import Image
from docx import Document as WordDocument
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app import document_files, pdf_ocr, rag, uploads
from backend.app.domain import Document as CatalogDocument
from backend.app.storage import documents as document_store
from backend.rag_index.ingest import Document, chunk_document, open_collection


@pytest.fixture
def local_index(monkeypatch):
    pytest.importorskip("chromadb")
    vector = [1.0] + [0.0] * 383
    encoder = Mock(side_effect=lambda texts, _prefix: [vector] * len(texts))
    monkeypatch.setattr(uploads, "embed", encoder)
    monkeypatch.setattr(rag, "embed", encoder)
    monkeypatch.setattr(rag, "manifest_collection", lambda: None)
    monkeypatch.setattr(rag, "_attempts", Mock())
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    return encoder


def pdf_file(text="", *, locked=False, large=False, pages=1):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        name = NameObject
        page[name("/Resources")] = DictionaryObject({
            name("/Font"): DictionaryObject({
                name("/F1"): DictionaryObject({
                    name("/Type"): name("/Font"),
                    name("/Subtype"): name("/Type1"),
                    name("/BaseFont"): name("/Helvetica"),
                }),
            }),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode("ascii"))
        page[name("/Contents")] = stream
    for _ in range(pages - 1):
        writer.add_blank_page(width=300, height=300)
    if locked:
        writer.encrypt("test-password")
    if large:
        writer.add_metadata({"/Subject": "x" * (70 * 1024)})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def word_file(text, cells=()):
    document = WordDocument()
    document.add_paragraph(text)
    if cells:
        table = document.add_table(rows=1, cols=len(cells))
        for cell, value in zip(table.rows[0].cells, cells, strict=True):
            cell.text = value
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def scanned_pdf():
    output = BytesIO()
    with Image.new("RGB", (240, 120), "white") as image:
        image.save(output, "PDF")
    return output.getvalue()


@pytest.mark.parametrize("extension", ["pdf", "docx"])
def test_binary_upload_preserves_source_and_exact_quote(client, local_index, monkeypatch, extension):
    filename = f"contract.{extension.upper()}"
    if extension == "pdf":
        monkeypatch.setattr(pdf_ocr, "engine", Mock(side_effect=AssertionError("Text PDF should skip OCR")))
        quote = "The service warranty lasts 37 days."
        content = pdf_file(quote, large=True)
        assert len(content) > 64 * 1024
    else:
        quote = "Гарантийный срок | 37 дней"
        content = word_file("Условия договора", ["Гарантийный срок", "37 дней"])

    async def answer(payload, _instructions, _api_key, schema, *_args, **_kwargs):
        if schema is rag.AnswerVerification:
            return rag.AnswerVerification(missing_parts=[], unsupported_claims=[])
        fragment = json.loads(payload)["fragments"][0]
        assert quote in fragment["text"]
        return rag.AskEvidenceDraft(answer="37 дней", answerable=True, citations=[fragment["chunk_id"]], evidence=[
            rag.AnswerEvidence(question_part="Срок гарантии", chunk_id=fragment["chunk_id"], quote=quote),
        ])

    monkeypatch.setattr(rag, "complete_structured", answer)
    response = client.post(f"/documents/upload?filename={filename}", content=content)
    assert response.status_code == 201
    record = response.json()
    assert record["source"] == filename and quote in record["text"]
    assert client.get(f"/documents/{record['id']}").json() == record
    assert client.post(f"/documents/upload?filename={filename}", content=content).json() == record
    assert local_index.call_count == 1

    result = client.post("/ask", json={"question": "Какой гарантийный срок?"})
    assert result.status_code == 200
    citation = result.json()["citations"][0]
    assert citation["source"] == filename and citation["doc_id"] == record["id"] and quote in citation["text"]
    assert citation.get("page_number") == (1 if extension == "pdf" else None)
    source = client.get(f"/ask/sources/{citation['chunk_id']}")
    assert source.status_code == 200 and source.json()["text"] == citation["text"]
    original = client.get(f"/documents/{record['id']}/original.pdf")
    if extension == "pdf":
        assert original.status_code == 200 and original.content == content
        assert original.headers["content-type"] == "application/pdf"
        assert original.headers["content-disposition"].startswith("inline;")
    else:
        assert original.status_code == 404


def test_mixed_pdf_indexes_ocr_and_cites_the_same_text(client, local_index, monkeypatch):
    writer = PdfWriter()
    writer.append(BytesIO(pdf_file("Terms of warranty")))
    writer.add_blank_page(width=300, height=300)
    writer.append(BytesIO(scanned_pdf()))
    output = BytesIO()
    writer.write(output)
    quote = "Срок гарантии составляет 37 дней."
    recognize = Mock(return_value=SimpleNamespace(txts=(quote,)))
    monkeypatch.setattr(pdf_ocr, "engine", lambda: recognize)

    async def answer(payload, _instructions, _api_key, schema, *_args, **_kwargs):
        if schema is rag.AnswerVerification:
            return rag.AnswerVerification(missing_parts=[], unsupported_claims=[])
        fragments = json.loads(payload)["fragments"]
        assert {fragment["text"] for fragment in fragments} == {"Terms of warranty", quote}
        fragment = next(fragment for fragment in fragments if fragment["text"] == quote)
        return rag.AskEvidenceDraft(answer="37 дней", answerable=True, citations=[fragment["chunk_id"]], evidence=[
            rag.AnswerEvidence(question_part="Срок гарантии", chunk_id=fragment["chunk_id"], quote=quote),
        ])

    monkeypatch.setattr(rag, "complete_structured", answer)
    uploaded = client.post("/documents/upload?filename=mayak-scan.pdf", content=output.getvalue())
    assert uploaded.status_code == 201
    assert uploaded.json()["text"] == f"Terms of warranty\n\n{quote}"
    recognize.assert_called_once()

    result = client.post("/ask", json={"question": "Какой срок гарантии?"})
    assert result.status_code == 200
    citation = result.json()["citations"][0]
    assert citation["source"] == "mayak-scan.pdf" and citation["text"] == quote
    assert citation["page_number"] == 3
    source = client.get(f"/ask/sources/{citation['chunk_id']}")
    assert source.status_code == 200 and source.json()["page_number"] == 3
    assert source.json()["before"] == "Terms of warranty\n\n"
    original = client.get(f"/documents/{uploaded.json()['id']}/original.pdf")
    assert original.status_code == 200 and original.content == output.getvalue()
    page = client.get(f"/documents/{uploaded.json()['id']}/original.pdf?page=3")
    assert page.status_code == 200
    preview = PdfReader(BytesIO(page.content))
    assert len(preview.pages) == 1 and len(preview.pages[0].images) == 1
    cover = client.get(f"/documents/{uploaded.json()['id']}/original.pdf?page=1")
    assert PdfReader(BytesIO(cover.content)).pages[0].extract_text() == "Terms of warranty"
    assert client.get(f"/documents/{uploaded.json()['id']}/original.pdf?page=4").status_code == 404


def test_reupload_attaches_legacy_pdf_and_text_edits_remove_only_page_links(client, local_index):
    filename = "Гарантия.pdf"
    text = "Warranty lasts 37 days."
    legacy_id = str(uuid5(NAMESPACE_URL, json.dumps([filename, text], ensure_ascii=False)))
    uploads.publish(CatalogDocument(legacy_id, "Warranty", text, filename))
    path = f"/documents/{legacy_id}"
    assert client.get(f"{path}/original.pdf").status_code == 404
    assert "page_number" not in rag.retrieve_question("warranty")[0]

    data = pdf_file(text)
    response = client.post("/documents/upload", params={"filename": filename}, content=data)
    assert response.status_code == 201 and response.json()["id"] == legacy_id
    assert rag.retrieve_question("warranty")[0]["page_number"] == 1
    assert client.patch(path, json={"title": "Renamed"}).status_code == 200
    assert rag.retrieve_question("warranty")[0]["page_number"] == 1

    response = client.patch(path, json={"text": "Warranty lasts 15 days."})
    assert response.status_code == 200
    assert "page_number" not in rag.retrieve_question("warranty")[0]
    original = client.get(f"{path}/original.pdf")
    assert original.status_code == 200 and original.content == data
    assert "%D0%93" in original.headers["content-disposition"]
    assert client.patch(path, json={"text": text}).status_code == 200
    assert rag.retrieve_question("warranty")[0]["page_number"] == 1
    assert client.delete(path).status_code == 204
    assert client.get(f"{path}/original.pdf").status_code == 404


def test_pdfs_with_same_text_keep_their_own_originals(client, local_index):
    first = pdf_file("Same text")
    second = pdf_file("Same text", large=True)
    path = "/documents/upload?filename=same.pdf"
    a = client.post(path, content=first)
    b = client.post(path, content=second)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert client.post(path, content=second).json() == b.json()
    for response, content in [(a, first), (b, second)]:
        doc_id = response.json()["id"]
        assert client.get(f"/documents/{doc_id}/original.pdf").content == content


@pytest.mark.parametrize(("mode", "status", "code"), [
    ("blank", 422, "upload_pdf_no_text"),
    ("failure", 503, "upload_ocr_unavailable"),
    ("over-limit", 413, "upload_ocr_pages"),
])
def test_scan_failure_does_not_publish_document(client, monkeypatch, mode, status, code):
    index = Mock()
    monkeypatch.setattr(uploads, "collection", index)
    recognize = Mock(return_value=SimpleNamespace(txts=None))
    monkeypatch.setattr(pdf_ocr, "engine", lambda: recognize)
    if mode == "failure":
        recognize.side_effect = RuntimeError("OCR unavailable")
    if mode == "over-limit":
        monkeypatch.setattr(document_files, "MAX_SCANNED_PAGES", 0)
    response = client.post("/documents/upload?filename=scan.pdf", content=scanned_pdf())
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert client.get("/documents").json() == []
    index.assert_not_called()


def test_uploaded_file_is_searchable_with_its_exact_source(client, local_index, monkeypatch):
    async def answer(payload, _instructions, _api_key, schema, *_args, **_kwargs):
        if schema is rag.AnswerVerification:
            return rag.AnswerVerification(missing_parts=[], unsupported_claims=[])
        fragments = json.loads(payload)["fragments"]
        assert len(fragments) == 1 and "37 дней" in fragments[0]["text"]
        fragment = fragments[0]
        return rag.AskEvidenceDraft(answer="37 дней", answerable=True, citations=[fragment["chunk_id"]], evidence=[
            rag.AnswerEvidence(question_part="Срок пропуска", chunk_id=fragment["chunk_id"], quote="37 дней"),
        ])

    monkeypatch.setattr(rag, "complete_structured", answer)
    path = "/documents/upload?filename=mayak.md"
    content = "# Маяк\n\nПропуск Маяк выдаётся на 37 дней.".encode("utf-8-sig")
    first = client.post(path, content=content)
    second = client.post(path, content=content)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert client.get("/documents").json() == [first.json()]
    assert local_index.call_count == 1

    response = client.post("/ask", json={"question": "На сколько дней выдаётся пропуск Маяк?"})
    assert response.status_code == 200
    citation = response.json()["citations"][0]
    assert citation["doc_id"] == first.json()["id"]
    assert citation["source"] == "mayak.md"
    source = client.get(f"/ask/sources/{citation['chunk_id']}")
    assert source.status_code == 200 and source.json()["text"] == citation["text"]


def test_index_failure_keeps_catalog_unchanged(client, local_index):
    local_index.side_effect = RuntimeError("vector index unavailable")
    response = client.post("/documents/upload?filename=mayak.txt", content=b"Test document")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upload_index_unavailable"
    assert client.get("/documents").json() == []
    assert uploads.active_corpus() == ({}, {}, set())


def test_catalog_failure_discards_staged_vectors(client, local_index, monkeypatch):
    save = Mock(side_effect=OSError("catalog write failed"))
    monkeypatch.setattr(document_store, "save_uploaded_document", save)

    response = client.post("/documents/upload?filename=mayak.txt", content=b"Test document")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upload_index_unavailable"
    assert client.get("/documents").json() == []
    assert uploads.collection().count() == 0
    save.assert_called_once()


def test_edit_and_delete_do_not_leave_searchable_old_text(client, local_index, monkeypatch):
    created = client.post("/documents/upload?filename=mayak.txt", content=b"Notice: 37 days").json()
    endpoint = f"/documents/{created['id']}"
    first = rag.retrieve_question("notice")[0]
    local_index.side_effect = RuntimeError("embedding failure")
    assert client.patch(endpoint, json={"text": "Notice: 15 days"}).status_code == 503
    assert client.get(endpoint).json()["text"] == "Notice: 37 days"
    local_index.side_effect = lambda texts, _prefix: [[1.0] + [0.0] * 383] * len(texts)

    assert client.patch(endpoint, json={"text": "Notice: 15 days"}).status_code == 200
    assert [hit["text"] for hit in rag.retrieve_question("notice")] == ["Notice: 15 days"]
    assert client.get(f"/ask/sources/{first['chunk_id']}").status_code == 404
    assert client.delete(endpoint).status_code == 204
    provider = AsyncMock()
    monkeypatch.setattr(rag, "complete_structured", provider)
    assert client.post("/ask", json={"question": "notice"}).json()["answerable"] is False
    provider.assert_not_awaited()


@pytest.mark.parametrize("edit", [True, False], ids=["edit", "delete"])
def test_catalog_changes_hide_imported_manifest_source(client, local_index, monkeypatch, tmp_path, edit):
    original = Document("mayak", "Mayak", "corpus/mayak.md", "2026-11-09", "Notice: 37 days")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"source": original.source}]), encoding="utf-8")
    monkeypatch.setattr(uploads, "MANIFEST", manifest)
    monkeypatch.setattr(rag, "load_corpus", lambda: {original.doc_id: original})
    index = open_collection(tmp_path / "manifest.chroma", create=True)
    chunk_id, text, metadata = next(chunk_document(original))
    index.upsert(ids=[chunk_id], documents=[text], embeddings=local_index([text], "passage"), metadatas=[metadata])
    monkeypatch.setattr(rag, "manifest_collection", lambda: index)
    created = client.post("/documents", json={
        "title": original.title, "source": original.source, "text": original.text,
    })
    assert created.status_code == 201
    endpoint = f"/documents/{created.json()['id']}"
    source_url = f"/ask/sources/{chunk_id}"
    assert [hit["text"] for hit in rag.retrieve_question("notice")] == [original.text]
    assert client.get(source_url).status_code == 200

    if edit:
        response = client.patch(endpoint, json={"text": "Notice: 15 days", "source": "new.md"})
        assert response.status_code == 200 and response.json()["id"] == created.json()["id"]
        assert client.get(endpoint).json()["source"] == "new.md"
        assert [hit["text"] for hit in rag.retrieve_question("notice")] == ["Notice: 15 days"]
    else:
        assert client.delete(endpoint).status_code == 204
        assert client.get(endpoint).status_code == 404
        assert rag.retrieve_question("notice") == []
    assert client.get(source_url).status_code == 404


@pytest.mark.parametrize(("name", "body", "status", "code"), [
    ("sample.doc", b"text", 415, "upload_format"),
    ("sample.pdf", b"%PDF-1.7\ninvalid", 422, "upload_unreadable"),
    ("sample.docx", b"text", 422, "upload_unreadable"),
    ("sample.txt", b"\xff", 415, "upload_encoding"),
    ("sample.md", b"  ", 422, "upload_empty"),
    ("sample.txt", b"x" * (64 * 1024 + 1), 413, "upload_too_large"),
])
def test_invalid_upload_is_rejected(client, monkeypatch, name, body, status, code):
    index = Mock()
    monkeypatch.setattr(uploads, "collection", index)
    response = client.post(f"/documents/upload?filename={name}", content=body)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert client.get("/documents").json() == []
    index.assert_not_called()


@pytest.mark.parametrize("case", ["no-text", "locked", "large-file", "large-text", "pages", "package"])
def test_binary_limits_fail_before_indexing(client, monkeypatch, case):
    index = Mock()
    monkeypatch.setattr(uploads, "collection", index)
    if case == "no-text":
        filename, body, status, code = "empty.pdf", pdf_file(), 422, "upload_pdf_no_text"
    elif case == "locked":
        filename, body, status, code = "locked.pdf", pdf_file("Example", locked=True), 422, "upload_pdf_locked"
    elif case == "large-file":
        filename, body, status, code = "large.pdf", b"x" * (10 * 1024 * 1024 + 1), 413, "upload_file_too_large"
    elif case == "large-text":
        filename, body, status, code = "long.docx", word_file("x" * (256 * 1024 + 1)), 413, "upload_text_too_large"
    elif case == "pages":
        filename, body, status, code = "many-pages.pdf", pdf_file(pages=151), 413, "upload_pdf_pages"
    else:
        package = BytesIO()
        with ZipFile(package, "w") as archive:
            for part in range(2001):
                archive.writestr(str(part), "")
        filename, body, status, code = "many-parts.docx", package.getvalue(), 413, "upload_word_package"
    response = client.post(f"/documents/upload?filename={filename}", content=body)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert client.get("/documents").json() == []
    index.assert_not_called()
