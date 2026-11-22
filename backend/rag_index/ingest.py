"""Validate a document manifest and build its local Chroma index."""

import argparse
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.json"
MANIFEST = Path(os.environ.get("KNOWLEDGE_DESK_MANIFEST") or DEFAULT_MANIFEST).expanduser().resolve()
DEMO_CORPUS = MANIFEST == DEFAULT_MANIFEST
DEFAULT_INDEX = ROOT / "data" / "chroma" if DEMO_CORPUS else MANIFEST.with_suffix(".chroma")
INDEX = Path(os.environ.get("KNOWLEDGE_DESK_INDEX") or DEFAULT_INDEX).expanduser().resolve()
COLLECTION = "knowledge-desk-orbita" if DEMO_CORPUS else (
    "knowledge-desk-" + hashlib.sha256(str(MANIFEST).encode("utf-8")).hexdigest()[:16]
)
MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSION = 384
TARGET = 400
OVERLAP = 50
CHUNK_SIZES = (300, TARGET, 600)
METADATA = {
    "model": MODEL,
    "revision": REVISION,
    "dimension": DIMENSION,
    "metric": "cosine",
    "chunking": f"paragraph-word-v1:{TARGET}:{OVERLAP}",
}
_embedding_lock = Lock()


def index_metadata(chunk_size: int) -> dict:
    if chunk_size not in CHUNK_SIZES:
        raise ValueError(f"Unsupported chunk size: {chunk_size}")
    return {**METADATA, "chunking": f"paragraph-word-v1:{chunk_size}:{OVERLAP}"}


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    source: str
    version_date: str
    text: str
    path: Path | None = None


def normalize(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", unicodedata.normalize("NFC", text))
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def load_corpus(*, report: list[dict] | None = None) -> dict[str, Document]:
    try:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{MANIFEST.name}: {exc}") from None
    if not isinstance(entries, list):
        raise ValueError(f"{MANIFEST.name}: expected a JSON array of documents")
    documents = {}
    contents = {}
    sources = set()
    paths = set()
    for entry in entries:
        filename = str(entry.get("path", entry.get("source", MANIFEST.name))) if isinstance(entry, dict) else MANIFEST.name
        try:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key].strip()
                                                 for key in ("doc_id", "title", "source", "version_date")):
                raise ValueError("nonempty doc_id, title, source and version_date are required")
            document, path = read_document_entry(entry, sources, paths, documents)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            reason = f"{filename}: {exc}"
            if report is None:
                raise ValueError(reason) from None
            report.append({"file": filename, "decision": "rejected", "reason": str(exc)})
            continue
        doc_id, title, source, version, text = document.doc_id, document.title, document.source, document.version_date, document.text
        body = text.removeprefix(f"# {title}").strip()
        if not body:
            if report is not None:
                report.append({"file": filename, "decision": "skipped", "reason": "empty text"})
            continue
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in contents:
            if report is not None:
                report.append({"file": filename, "decision": "skipped", "reason": f"duplicate of {contents[digest]}"})
            continue
        contents[digest] = filename
        sources.add(source)
        paths.add(path)
        documents[doc_id] = document
        if report is not None:
            report.append({"file": filename, "decision": "accepted", "reason": "valid document"})
    return documents


def read_document_entry(entry, sources: set, paths: set, documents: dict) -> tuple[Document, Path]:
    doc_id, title, source = (entry[key] for key in ("doc_id", "title", "source"))
    version = date.fromisoformat(entry["version_date"]).isoformat()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", doc_id) or doc_id in documents:
        raise ValueError(f"Invalid or repeated doc_id: {doc_id}")
    if not title.strip() or not source.strip() or source in sources:
        raise ValueError(f"Invalid or repeated source for {doc_id}")
    if "path" not in entry and DEMO_CORPUS:
        # The original demo manifest stores repository-relative paths in source.
        path = (ROOT / source).resolve()
        allowed_root = ROOT / "data" / "documents"
    else:
        path = (MANIFEST.parent / entry["path"]).resolve()
        allowed_root = MANIFEST.parent
    if not path.is_relative_to(allowed_root) or path in paths or path.suffix.lower() not in {".md", ".txt"}:
        raise ValueError(f"Invalid or repeated document path for {doc_id}: {path}")
    text = normalize(path.read_text(encoding="utf-8-sig"))
    if "\ufffd" in text:
        raise ValueError(f"Unreadable document: {doc_id}")
    return Document(doc_id, title, source, version, text, path), path


def chunk_document(document: Document, *, chunk_size: int = TARGET):
    chunking = index_metadata(chunk_size)["chunking"]
    text = document.text
    version_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    start = 0
    ordinal = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if len(text) - end <= OVERLAP:
            end = len(text)
        else:
            paragraph_end = text.rfind("\n\n", start + chunk_size - 100, end + 1)
            word_end = text.rfind(" ", end - OVERLAP, end + 1)
            end = paragraph_end if paragraph_end >= 0 else word_end
            if end < 0:
                end = start + chunk_size
        while end > start and text[end - 1].isspace():
            end -= 1
        fingerprint = json.dumps([
            document.doc_id, document.version_date, version_hash, chunking, ordinal, start, end,
        ])
        suffix = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
        yield f"{document.doc_id}-{ordinal:03d}-{suffix}", text[start:end], {
            "doc_id": document.doc_id, "ordinal": ordinal, "start": start, "end": end,
            "source": document.source, "version_date": document.version_date,
        }
        if end == len(text):
            break
        next_start = end - OVERLAP
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        while text[next_start].isspace():
            next_start += 1
        start = next_start
        ordinal += 1


@lru_cache(maxsize=1)
def embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        MODEL, revision=REVISION, device="cpu",
        cache_folder=os.getenv("KNOWLEDGE_DESK_EMBEDDING_CACHE") or str(ROOT / "data" / ".model-cache"),
    )


def embed(texts: list[str], prefix: str) -> list[list[float]]:
    with _embedding_lock:
        vectors = embedding_model().encode(
            [f"{prefix}: {text}" for text in texts], normalize_embeddings=True, show_progress_bar=False,
        )
    if vectors.shape != (len(texts), DIMENSION):
        raise ValueError(f"Unexpected embedding shape: {vectors.shape}")
    return vectors.tolist()


def open_collection(path: Path, *, create: bool = False, name: str = COLLECTION, chunk_size: int = TARGET):
    import chromadb
    from chromadb.config import Settings

    metadata = index_metadata(chunk_size)
    client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
    if create:
        collection = client.get_or_create_collection(
            name, metadata=metadata, embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
    else:
        collection = client.get_collection(name, embedding_function=None)
    if collection.metadata != metadata or collection.configuration["hnsw"]["space"] != "cosine":
        raise ValueError("Index settings differ. Use a new index directory to rebuild.")
    return collection


def ingest(path: Path, *, chunk_size: int = TARGET) -> dict:
    start = perf_counter()
    decisions = []
    try:
        documents = load_corpus(report=decisions)
    except ValueError as exc:
        documents = {}
        decisions.append({"file": MANIFEST.name, "decision": "rejected", "reason": str(exc)})
    rejected = any(row["decision"] == "rejected" for row in decisions)
    if rejected or not documents:
        before = open_collection(path, chunk_size=chunk_size).count() if path.exists() else 0
        return {"status": "rejected" if rejected else "skipped", "documents": 0, "chunks": before,
                "upserted": 0, "removed": 0, "before": before, "after": before, "files": decisions}
    chunks = {
        chunk_id: (text, metadata)
        for document in documents.values()
        for chunk_id, text, metadata in chunk_document(document, chunk_size=chunk_size)
    }
    if not chunks:
        raise ValueError("Corpus produced no chunks.")
    max_tokens = None
    if chunk_size != TARGET:
        model = embedding_model()
        token_ids = model.tokenizer(
            [f"passage: {text}" for text, _ in chunks.values()], truncation=False,
        )["input_ids"]
        max_tokens = max(map(len, token_ids))
        if max_tokens > model.max_seq_length:
            raise ValueError(f"A passage has {max_tokens} tokens, exceeding {model.max_seq_length}.")
    collection = open_collection(path, create=True, chunk_size=chunk_size)
    rows = collection.get(include=["documents", "metadatas"])
    existing = {
        chunk_id: (text, metadata)
        for chunk_id, text, metadata in zip(rows["ids"], rows["documents"], rows["metadatas"], strict=True)
    }
    changed = [chunk_id for chunk_id, row in chunks.items() if existing.get(chunk_id) != row]
    if changed:
        texts = [chunks[chunk_id][0] for chunk_id in changed]
        collection.upsert(
            ids=changed, documents=texts, embeddings=embed(texts, "passage"),
            metadatas=[chunks[chunk_id][1] for chunk_id in changed],
        )
    stale = sorted(existing.keys() - chunks.keys())
    if stale:
        collection.delete(ids=stale)
    return {"status": "complete", "files": decisions, "before": len(existing), "after": collection.count(),
            "documents": len(documents), "chunks": collection.count(), "upserted": len(changed), "removed": len(stale),
            "build_ms": round((perf_counter() - start) * 1000), "max_passage_tokens": max_tokens}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--chunk-size", type=int, choices=CHUNK_SIZES, default=TARGET)
    args = parser.parse_args()
    if args.chunk_size != TARGET and args.index is None:
        parser.error("--index is required for a non-default chunk size")
    result = ingest(args.index or INDEX, chunk_size=args.chunk_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
