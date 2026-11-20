import asyncio
from decimal import Decimal

import httpx
import pytest

from backend.app import eval_budget, summarizer
from backend.app.models import RequestSummary

from .conftest import message, snapshot

TEXT = "Вымышленный вопрос"
INSTRUCTIONS = "Подготовь учебное резюме."
OUTPUT = 12
ANSWER = {"summary": "Учебное резюме.", "request_type": "course_question", "needs_human": False}


def policy(amount, **overrides):
    return eval_budget.EvalBudget(**{"usd": Decimal(amount), "max_calls": 3, "max_input_tokens": 24000,
                                    "max_output_tokens": OUTPUT, "max_seconds": 10, **overrides})


def complete(budget):
    return asyncio.run(summarizer.complete_structured(
        TEXT, INSTRUCTIONS, "fixture", RequestSummary, OUTPUT, "invalid_summary",
        model=eval_budget.MODEL, budget=budget,
    ))


def test_zero_budget_stops_before_provider(security):
    before = snapshot()
    with pytest.raises(summarizer.SummaryError, match="budget_exhausted"):
        complete(policy("0"))
    security.provider.assert_not_awaited()
    security.outgoing.assert_not_awaited()
    assert snapshot() == before


def test_small_budget_bills_one_call_and_denies_the_next(security):
    estimated, charge = policy("1").reserve(eval_budget.MODEL, TEXT, INSTRUCTIONS, RequestSummary.model_json_schema(), OUTPUT)
    budget = policy(charge)
    reply = message(ANSWER).json()
    reply["usage"] = {"input_tokens": estimated, "output_tokens": OUTPUT, "total_tokens": estimated + OUTPUT,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    security.provider.side_effect = [httpx.Response(200, json=reply)]
    assert complete(budget).summary == ANSWER["summary"]
    with pytest.raises(summarizer.SummaryError, match="budget_exhausted"):
        complete(budget)
    security.provider.assert_awaited_once()
    assert budget.calls == 1 and budget.spent == charge and budget.held == 0


def test_provider_timeout_holds_budget_without_retry(security):
    _, cost = policy("1").reserve(eval_budget.MODEL, TEXT, INSTRUCTIONS, RequestSummary.model_json_schema(), OUTPUT)
    budget = policy(cost)
    security.provider.side_effect = httpx.ReadTimeout("fixture-only timeout")
    with pytest.raises(summarizer.SummaryError) as failed:
        complete(budget)
    assert failed.value.code == "provider_timeout"
    assert budget.held == cost and budget.report()["total_usd"] is None
    with pytest.raises(summarizer.SummaryError, match="budget_exhausted"):
        complete(budget)
    security.provider.assert_awaited_once()
    security.outgoing.assert_not_awaited()


def test_eval_deadline_stops_before_provider(security, monkeypatch):
    monkeypatch.setattr(eval_budget, "monotonic", lambda: 20)
    budget = policy("1", started=0)
    with pytest.raises(summarizer.SummaryError, match="eval_deadline"):
        complete(budget)
    security.provider.assert_not_awaited()
