"""Recompute quality, latency and observed token costs from reviewed RAG results."""

import argparse
import json
from math import ceil
from pathlib import Path
from statistics import median

from .eval_rag import apply_reviews, result_digest

PRICING = {"date": "2026-11-20", "currency": "USD", "per_million_input": 0.40,
           "per_million_cached_input": 0.10, "per_million_output": 1.60,
           "source": "https://developers.openai.com/api/docs/models/gpt-4.1-mini#pricing"}


def metrics(cases: list[dict]) -> dict:
    positive = [case for case in cases if case["answerable"]]
    negative = [case for case in cases if not case["answerable"]]
    reviews = [case["manual_review"] for case in cases if case.get("manual_review")]
    for case in cases:
        if case.get("manual_review") and case["manual_review"]["result_sha256"] != case["result_sha256"]:
            raise ValueError(f"Stale review for {case['id']}")
        if case["result"] and result_digest(case["result"]) != case["result_sha256"]:
            raise ValueError(f"Result hash mismatch for {case['id']}")
    durations = sorted(case["duration_ms"] for case in cases)
    successful_answers = [case for case in positive if (case.get("manual_review") or {}).get("grade") == "pass"]
    actual_answers = [case for case in cases if case["result"] and case["result"]["answerable"]]
    sources = [(set(case["expected_doc_ids"]), {hit["doc_id"] for hit in case["retrieved"]}) for case in positive]
    responses = [case[field] for case in cases for field in ("provider", "verification_provider") if case.get(field)]
    usage = [row["usage"] for row in responses if row.get("usage")]
    input_tokens = sum(row["input_tokens"] for row in usage)
    cached_tokens = sum((row.get("input_tokens_details") or {}).get("cached_tokens", 0) for row in usage)
    output_tokens = sum(row["output_tokens"] for row in usage)
    observed_cost = ((input_tokens - cached_tokens) * PRICING["per_million_input"]
                     + cached_tokens * PRICING["per_million_cached_input"]
                     + output_tokens * PRICING["per_million_output"]) / 1_000_000
    incomplete_usage = len(usage) != len(responses) or any(case["error_code"] for case in cases)
    correct_refusals = [case for case in negative if case["result"] and not case["result"]["answerable"]
                        and not case["result"]["citations"] and (case.get("manual_review") or {}).get("grade") == "pass"]
    return {
        "cases": len(cases), "positive_cases": len(positive), "negative_cases": len(negative),
        "reviewed": len(reviews), "manual_passes": sum(review["grade"] == "pass" for review in reviews),
        "retrieval_hit_at_5": sum(bool(expected & found) for expected, found in sources) / len(positive) if positive else None,
        "all_expected_sources_at_5": sum(expected <= found for expected, found in sources),
        "confirmed_answers": len(successful_answers), "returned_positive_answers": len(actual_answers),
        "confirmed_answer_precision": len(successful_answers) / len(actual_answers) if actual_answers else None,
        "correct_refusals": len(correct_refusals),
        "refusal_recall": len(correct_refusals) / len(negative) if negative else None,
        "median_ms": median(durations), "p95_ms": durations[ceil(len(durations) * 0.95) - 1],
        "errors": {code: sum(case["error_code"] == code for case in cases)
                   for code in sorted({case["error_code"] for case in cases if case["error_code"]})},
        "provider_responses": len(responses), "usage_responses": len(usage),
        "input_tokens": input_tokens, "cached_input_tokens": cached_tokens, "output_tokens": output_tokens,
        "observed_token_cost_usd": round(observed_cost, 8),
        "total_cost_usd": None if incomplete_usage else round(observed_cost, 8),
        "billing_total": "unknown", "usage_complete": not incomplete_usage,
    }


def summarize(report: dict) -> dict:
    cases = report["cases"]
    return {"pricing": PRICING, "overall": metrics(cases),
            "splits": {split: metrics([case for case in cases if case.get("split") == split])
                       for split in sorted({case["split"] for case in cases if "split" in case})}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    reviews_path = args.report.with_suffix(".reviews.json")
    if reviews_path.exists():
        apply_reviews(report, json.loads(reviews_path.read_text(encoding="utf-8")))
    summary = summarize(report)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
