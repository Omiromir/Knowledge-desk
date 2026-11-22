"""Copy existing Chroma fragments into pgvector without changing their IDs."""

import argparse
import json
from pathlib import Path
from struct import pack

from backend.rag_index.ingest import CHUNK_SIZES, COLLECTION, DIMENSION, load_corpus, normalize, open_collection
from backend.rag_index.retrieve import source_record

from .app import database, postgres, uploads, vector_index
from .app.domain import Document


def entries(collection):
    for offset in range(0, collection.count(), 256):
        rows = collection.get(limit=256, offset=offset, include=["documents", "metadatas", "embeddings"])
        yield from zip(rows["ids"], rows["documents"], rows["metadatas"], rows["embeddings"], strict=True)


def manifest_rows(connection, indexes):
    corpus = load_corpus()
    catalog = {}
    for row in connection.execute("""SELECT d.id, d.title, d.text, d.source FROM documents d
        WHERE NOT EXISTS (SELECT 1 FROM document_uploads u WHERE u.document_id = d.id)
        AND NOT EXISTS (SELECT 1 FROM hidden_manifest_sources h WHERE h.collection = ? AND h.source = d.source)""",
                                  (COLLECTION,)).fetchall():
        signature = row["source"], row["title"], normalize(row["text"])
        catalog.setdefault(signature, []).append(row["id"])

    for profile, path in indexes:
        collection = open_collection(path.resolve(strict=True), chunk_size=profile)
        for cid, text, meta, vector in entries(collection):
            source_record(cid, text, meta, corpus)
            doc = corpus[meta["doc_id"]]
            matches = catalog.get((doc.source, doc.title, doc.text), [])
            if len(matches) > 1:
                raise ValueError(f"Ambiguous catalog mapping for {doc.doc_id}")
            yield vector_index.chunk_row(cid, matches[0] if matches else None, profile, text, meta, vector)


def upload_rows(connection, path):
    catalog = {}
    versions = {}
    for row in connection.execute("""SELECT d.id, d.title, d.text, d.source, u.version_date, u.index_key
                                     FROM document_uploads u JOIN documents d ON d.id = u.document_id""").fetchall():
        doc = Document(row["id"], row["title"], row["text"], row["source"])
        catalog[doc.id] = uploads.corpus_document(doc, row["version_date"])
        versions[doc.id] = row["index_key"]
    if not path.is_dir() and not versions:
        return [], 0

    result = []
    covered = set()
    skipped = 0
    for cid, text, meta, vector in entries(open_collection(path.resolve(strict=True), name=uploads.COLLECTION)):
        doc_id = meta["doc_id"]
        if doc_id not in versions or meta["index_key"] != versions[doc_id]:
            skipped += 1
            continue
        source_record(cid, text, meta, catalog)
        result.append(vector_index.chunk_row(cid, doc_id, 0, text, meta, vector))
        covered.add(doc_id)
    if covered != versions.keys():
        raise ValueError("Published uploads are missing from the Chroma source")
    return result, skipped


def verify(connection, expected):
    if not expected:
        raise ValueError("Empty source index")
    rows = connection.execute("""SELECT chunk_id, document_id, doc_id, profile, index_key, source, version_date,
               ordinal, start_offset, end_offset, page_number, text, embedding::text AS embedding
               FROM rag_chunks WHERE chunk_id = ANY(?)""", (list(expected),)).fetchall()
    if len(rows) != len(expected):
        raise ValueError("The destination is missing source fragment IDs")
    for row in rows:
        before = expected[row["chunk_id"]]
        after = tuple(row.values())
        if before[:-1] != after[:-1] or pack(f"={DIMENSION}f", *json.loads(before[-1])) != pack(
            f"={DIMENSION}f", *json.loads(after[-1]),
        ):
            raise ValueError(f"Destination does not match source chunk {row['chunk_id']}")


def migrate(indexes, upload_index: Path):
    if not postgres.database_url():
        raise ValueError("Set KNOWLEDGE_DESK_DATABASE_URL")
    with database.connect() as connection:
        connection.execute("LOCK TABLE documents, document_uploads, hidden_manifest_sources IN SHARE MODE")
        base = list(manifest_rows(connection, indexes))
        uploaded, skipped = upload_rows(connection, upload_index)
        rows = base + uploaded
        expected = {row[0]: row for row in rows}
        if len(expected) != len(rows):
            raise ValueError("Duplicate Chroma fragment IDs across collections")
        if rows:
            connection.executemany(vector_index.INSERT, rows)
        verify(connection, expected)
    return {
        "manifest": {size: sum(row[3] == size for row in base) for size, _ in indexes},
        "upload_fragments": len(uploaded),
        "unbound_manifest_fragments": sum(row[1] is None for row in base),
        "skipped_inactive_upload_fragments": skipped,
        "verified_ids_and_vectors": len(expected),
    }


def index_argument(value: str):
    size, separator, path = value.partition("=")
    if not separator or not path or size not in {str(size) for size in CHUNK_SIZES}:
        raise argparse.ArgumentTypeError("Use SIZE=PATH, with size 300, 400 or 600")
    return int(size), Path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-index", action="append", type=index_argument, required=True)
    parser.add_argument("--upload-index", type=Path, default=database.DB_PATH.with_suffix(".chroma"))
    args = parser.parse_args()
    if len({size for size, _ in args.manifest_index}) != len(args.manifest_index):
        parser.error("Specify one source path per profile")
    print(json.dumps(migrate(args.manifest_index, args.upload_index), indent=2))


if __name__ == "__main__":
    main()
