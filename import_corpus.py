"""Import the configured corpus through the existing documents API."""

import argparse
import json
from urllib.request import Request, urlopen

from backend.rag_index.ingest import load_corpus


def api_call(url, method="GET", payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    with urlopen(Request(url, data=data, headers=headers, method=method), timeout=10) as response:
        return json.load(response)


def import_corpus(api_url: str) -> dict:
    endpoint = api_url.rstrip("/") + "/documents"
    corpus = load_corpus()
    by_source = {doc.source: [] for doc in corpus.values()}
    for record in api_call(endpoint):
        if record["source"] in by_source:
            by_source[record["source"]].append(record)
    if any(len(records) > 1 for records in by_source.values()):
        raise ValueError("API contains multiple records with the same manifest source. Resolve duplicates first.")
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    ids = {}
    for doc in corpus.values():
        payload = {"title": doc.title, "text": doc.text, "source": doc.source}
        matches = by_source[doc.source]
        if not matches:
            record = api_call(endpoint, "POST", payload)
            counts["created"] += 1
        else:
            record = matches[0]
            if any(record[key] != value for key, value in payload.items()):
                record = api_call(f"{endpoint}/{record['id']}", "PATCH", payload)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
        ids[doc.doc_id] = record["id"]
    return {**counts, "api_ids_by_doc_id": ids}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    print(json.dumps(import_corpus(parser.parse_args().api), ensure_ascii=False, indent=2))
