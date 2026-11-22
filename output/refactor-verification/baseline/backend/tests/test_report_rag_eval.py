import pytest

from backend.eval_rag import result_digest
from backend.report_rag_eval import metrics


def case(*, answerable=True, result=True, grade="pass", duration=100, found=True, error=None):
    response = {"answer": "Текст", "answerable": result, "citations": []} if result is not None else None
    digest = result_digest(response) if response else None
    return {"id": "test", "answerable": answerable, "expected_doc_ids": ["source"] if answerable else [],
            "retrieved": [{"doc_id": "source"}] if found else [], "result": response,
            "result_sha256": digest, "manual_review": {"grade": grade, "result_sha256": digest},
            "duration_ms": duration, "error_code": error, "provider": None, "verification_provider": None}


def test_metrics_count_partial_answers_and_timeouts_as_failures():
    rows = [case(), case(grade="partial", duration=200, found=False),
            case(answerable=False, result=False, duration=300),
            case(result=None, grade="fail", duration=20000, error="provider_timeout")]
    row = metrics(rows)
    assert row["cases"] == 4 and row["manual_passes"] == 2
    assert row["retrieval_hit_at_5"] == 2 / 3
    assert row["confirmed_answers"] == 1 and row["confirmed_answer_precision"] == 0.5
    assert row["correct_refusals"] == 1 and row["refusal_recall"] == 1
    assert row["median_ms"] == 250 and row["p95_ms"] == 20000
    assert row["total_cost_usd"] is None and not row["usage_complete"]


def test_cost_uses_returned_usage_and_cached_input_rate():
    row = case()
    row["provider"] = {"usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200},
                                  "output_tokens": 100}}
    totals = metrics([row])
    assert totals["input_tokens"] == 1000 and totals["cached_input_tokens"] == 200
    assert totals["observed_token_cost_usd"] == totals["total_cost_usd"] == 0.0005
    assert totals["billing_total"] == "unknown"
    row["verification_provider"] = {"usage": None}
    assert metrics([row])["total_cost_usd"] is None


def test_stale_review_is_rejected():
    row = case()
    row["manual_review"]["result_sha256"] = "old"
    with pytest.raises(ValueError, match="Stale review"):
        metrics([row])
