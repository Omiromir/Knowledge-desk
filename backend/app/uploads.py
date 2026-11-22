"""Publish uploaded documents and their searchable revisions together."""

import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from backend.rag_index.ingest import Document as CorpusDocument
from backend.rag_index.ingest import COLLECTION as MANIFEST_COLLECTION
from backend.rag_index.ingest import MANIFEST, chunk_document, embed, normalize, open_collection

from . import database, originals, postgres, vector_index
from .document_files import MAX_EXTRACTED_BYTES, read_file
from .domain import Document, NotFoundError
from .services import DocumentService
from .storage import documents as document_store
from .summarizer import SummaryError

logger = logging.getLogger(__name__)
CHUNK_PREFIX = "upload:"
COLLECTION = "knowledge-desk-uploads"
_writes = RLock()


def collection(*, create: bool = False):
    path = database.DB_PATH.resolve().with_suffix(".chroma")
    return open_collection(path, name=COLLECTION, create=create)


def corpus_document(record, version_date: str) -> CorpusDocument:
    return CorpusDocument(record.id, record.title, record.source, version_date, normalize(record.text))


def active_corpus() -> tuple[dict[str, CorpusDocument], dict[str, str], set[str]]:
    documents, versions = {}, {}
    rows, hidden = document_store.index_visibility(MANIFEST_COLLECTION)
    for row in rows:
        record = Document(row["id"], row["title"], row["text"], row["source"])
        documents[record.id] = corpus_document(record, row["version_date"])
        versions[record.id] = row["index_key"]
    return documents, versions, hidden


def manifest_source(record: Document) -> tuple[str, str] | None:
    if MANIFEST.is_file():
        entries = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        if any(entry["source"] == record.source for entry in entries):
            return MANIFEST_COLLECTION, record.source
    return None


def discard(index, index_key: str) -> None:
    try:
        index.delete(where={"index_key": index_key})
    except Exception as exc:
        logger.warning("Could not remove inactive upload index data: %s", type(exc).__name__)


def upload_chunks(doc: CorpusDocument, spans: list[tuple[int, int, int]] | None):
    if not spans:
        yield from chunk_document(doc)
        return
    ordinal = 0
    for number, start, end in spans:
        page = replace(doc, text=doc.text[start:end])
        for chunk_id, text, meta in chunk_document(page):
            yield f"p{number}:{chunk_id}", text, {
                **meta, "page_number": number, "ordinal": ordinal,
                "start": start + meta["start"], "end": start + meta["end"],
            }
            ordinal += 1


def publish(
    record: Document, previous_key: str | None = None, *,
    update: bool = False, hidden_source: tuple[str, str] | None = None,
    pdf: tuple[str, bytes] | None = None, spans: list[tuple[int, int, int]] | None = None,
    owner_id: str | None = None,
) -> None:
    date = datetime.now(timezone.utc).date().isoformat()
    doc = corpus_document(record, date)
    revision = [doc.doc_id, doc.title, doc.source, doc.version_date, doc.text]
    if spans:
        revision.append(spans)
    payload = json.dumps(revision, ensure_ascii=False)
    index_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    index = None
    try:
        if index_key == previous_key:
            document_store.save_uploaded_document(record, date, index_key, update=True)
            return
        rows = list(upload_chunks(doc, spans))
        texts = [text for _, text, _ in rows]
        ids = [f"{CHUNK_PREFIX}{index_key[:20]}:{chunk_id}" for chunk_id, _, _ in rows]
        metadata = [{**meta, "index_key": index_key} for _, _, meta in rows]
        vectors = embed(texts, "passage")
        chunks = None
        if postgres.database_url():
            chunks = [vector_index.chunk_row(cid, record.id, 0, text, meta, vector)
                      for cid, text, meta, vector in zip(ids, texts, metadata, vectors, strict=True)]
        else:
            index = collection(create=True)
            index.upsert(ids=ids, documents=texts, embeddings=vectors, metadatas=metadata)
        original = None
        if pdf is not None:
            original = (*pdf, hashlib.sha256(doc.text.encode("utf-8")).hexdigest(), json.dumps(spans))
        document_store.save_uploaded_document(
            record, date, index_key, update=update, hidden_source=hidden_source, pdf=original, owner_id=owner_id,
            chunks=chunks,
        )
    except originals.StorageError as exc:
        logger.warning("PDF original upload failed: %s", type(exc).__name__)
        raise SummaryError("upload_storage_unavailable", "Хранилище оригиналов PDF временно недоступно.") from None
    except Exception as exc:
        if index is not None:
            discard(index, index_key)
        logger.error("Upload indexing failed: %s", type(exc).__name__)
        raise SummaryError("upload_index_unavailable", "Could not index and save the document.") from None
    if previous_key and index is not None:
        discard(index, previous_key)


def add(filename: str, content: bytes, *, owner_id: str | None = None) -> Document:
    text, spans = read_file(filename, content)
    prefix = f"{owner_id}:" if owner_id else ""
    identity = prefix + json.dumps([filename, normalize(text)], ensure_ascii=False)
    record = Document(str(uuid5(NAMESPACE_URL, identity)), Path(filename).stem.strip() or filename, text, filename)
    pdf = (filename, content) if spans else None
    with _writes:
        existing = document_store.get_document(record.id)
        if pdf is None and existing:
            return existing
        if pdf:
            # PDFs with the same extracted text can have different originals or page layouts.
            file_identity = prefix + "pdf:" + json.dumps([filename, hashlib.sha256(content).hexdigest()])
            file_id = str(uuid5(NAMESPACE_URL, file_identity))
            saved = document_store.get_document(file_id)
            if saved:
                return saved
            if existing:
                original = document_store.get_pdf(existing.id)
                if original and originals.matches(original, content):
                    return existing
                if original is None and existing.source == filename and normalize(existing.text) == normalize(text):
                    version = document_store.uploaded_version(existing.id)
                    publish(existing, version["index_key"] if version else None, update=True, pdf=pdf, spans=spans)
                    return existing
            record = replace(record, id=file_id)
        publish(record, pdf=pdf, spans=spans or None, owner_id=owner_id)
    return record


def update(service: DocumentService, document_id: str, changes: dict[str, str]) -> Document:
    with _writes:
        version = document_store.uploaded_version(document_id)
        original = service.get(document_id)
        cloud = bool(postgres.database_url())
        hidden_source = manifest_source(original) if version is None and not cloud else None
        is_manifest = vector_index.has_manifest_chunks(document_id) if cloud and version is None else bool(hidden_source)
        if version is None and not is_manifest:
            return service.update(document_id, changes)
        record = replace(original, **changes)
        if record == original:
            return original
        if len(record.text.encode("utf-8")) > MAX_EXTRACTED_BYTES:
            raise SummaryError("upload_text_too_large", "The text exceeds 256 KiB.")
        # Keep page links only while the indexed text still matches the original PDF.
        text_hash = hashlib.sha256(normalize(record.text).encode("utf-8")).hexdigest()
        spans = document_store.pdf_spans(document_id, text_hash)
        publish(
            record, version["index_key"] if version else None, update=True, hidden_source=hidden_source, spans=spans,
        )
        return record


def delete(service: DocumentService, document_id: str) -> None:
    with _writes:
        if postgres.database_url():
            if not document_store.delete_document(document_id):
                raise NotFoundError("Document not found.")
            return
        version = document_store.uploaded_version(document_id)
        record = service.get(document_id)
        hidden_source = manifest_source(record) if version is None else None
        if not document_store.delete_document(document_id, hidden_source=hidden_source):
            raise NotFoundError("Document not found.")
        if version is not None:
            try:
                discard(collection(), version["index_key"])
            except Exception as exc:
                logger.warning("Could not open the upload index to remove a deleted revision: %s", type(exc).__name__)
