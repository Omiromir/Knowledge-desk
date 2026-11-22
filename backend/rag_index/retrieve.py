"""Search local Chroma with E5 query embeddings or resolve a chunk to its source."""

import argparse
import json
from pathlib import Path

from .ingest import CHUNK_SIZES, INDEX, ROOT, TARGET, embed, load_corpus, open_collection

CASES = ROOT / "data" / "retrieval-cases.json"
TOP_K = 5


def source_record(chunk_id, text, metadata, corpus):
    document = corpus[metadata["doc_id"]]
    start, end = metadata["start"], metadata["end"]
    if (
        not 0 <= start < end <= len(document.text)
        or document.text[start:end] != text
        or document.source != metadata["source"]
        or document.version_date != metadata["version_date"]
    ):
        raise ValueError(f"Source no longer matches {chunk_id}. Rebuild the index.")
    return {"chunk_id": chunk_id, "text": text, **metadata, "title": document.title}


def resolve_chunk(collection, chunk_id: str, corpus: dict | None = None, *, where=None) -> dict:
    rows = collection.get(ids=[chunk_id], where=where, include=["documents", "metadatas", "embeddings"])
    if not rows["ids"]:
        raise LookupError(f"Unknown chunk_id: {chunk_id}")
    corpus = load_corpus() if corpus is None else corpus
    record = source_record(chunk_id, rows["documents"][0], rows["metadatas"][0], corpus)
    document = corpus[record["doc_id"]]
    return {
        **record,
        "embedding_dimension": len(rows["embeddings"][0]),
        "document_text": document.path.read_text(encoding="utf-8-sig") if document.path else document.text,
    }


def search(collection, questions: list[str], corpus: dict, *, vectors=None, where=None, top_k: int = TOP_K) -> list[list[dict]]:
    count = min(collection.count(), top_k)
    if not count or not questions:
        return [[] for _ in questions]
    rows = collection.query(
        query_embeddings=embed(questions, "query") if vectors is None else vectors,
        n_results=count, where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        [
            {**source_record(chunk_id, text, metadata, corpus), "distance": distance}
            for chunk_id, text, metadata, distance in zip(ids, texts, metas, distances, strict=True)
        ]
        for ids, texts, metas, distances in zip(
            rows["ids"], rows["documents"], rows["metadatas"], rows["distances"], strict=True,
        )
    ]


def evaluate(collection, cases_path: Path, corpus: dict) -> dict:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    case_ids = set()
    for case in cases:
        expected = set(case["expected_doc_ids"])
        if case["id"] in case_ids or not case["question"].strip() or not expected or not expected <= corpus.keys():
            raise ValueError(f"Invalid retrieval case: {case['id']}")
        case_ids.add(case["id"])
    if not cases:
        raise ValueError("No retrieval cases.")
    results = search(collection, [case["question"] for case in cases], corpus)
    evaluations = []
    for case, hits in zip(cases, results, strict=True):
        rank = next(
            (i for i, hit in enumerate(hits, 1) if hit["doc_id"] in case["expected_doc_ids"]),
            None,
        )
        evaluations.append({**case, "hit": rank is not None, "first_relevant_rank": rank, "top_5": hits})
    hit_count = sum(case["hit"] for case in evaluations)
    return {
        **collection.metadata, "k": TOP_K, "hit_count": hit_count, "question_count": len(cases),
        "hit_at_5": hit_count / len(cases), "cases": evaluations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--chunk-size", type=int, choices=CHUNK_SIZES, default=TARGET)
    parser.add_argument("--top-k", type=int, choices=(TOP_K, 20), default=TOP_K)
    parser.add_argument("--output", type=Path, help="Write UTF-8 JSON to a file instead of stdout.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--question", help="Search for one question.")
    mode.add_argument("--chunk", help="Show a chunk and its complete source document.")
    mode.add_argument("--cases", type=Path, default=CASES, help="Evaluate questions from this JSON file.")
    args = parser.parse_args()
    if args.top_k != TOP_K and args.question is None:
        parser.error("--top-k 20 requires --question")
    collection = open_collection(args.index, chunk_size=args.chunk_size)
    if args.chunk:
        result = resolve_chunk(collection, args.chunk)
    elif args.question is not None:
        if not args.question.strip():
            parser.error("--question cannot be empty")
        result = {"metric": collection.metadata["metric"], "question": args.question,
                  f"top_{args.top_k}": search(collection, [args.question.strip()], load_corpus(), top_k=args.top_k)[0]}
    else:
        result = evaluate(collection, args.cases, load_corpus())
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
