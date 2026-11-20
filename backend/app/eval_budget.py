"""Conservative reservations for a serial, explicitly authorized paid eval."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic

MODEL = "gpt-4.1-mini-2025-04-14"
PRICES = (Decimal("0.40"), Decimal("0.10"), Decimal("1.60"))
PRICING_DATE = "2026-11-21"
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini#pricing"
FRAMING_ALLOWANCE = 512


def price(input_tokens: int, output_tokens: int, cached: int = 0) -> Decimal:
    return ((input_tokens - cached) * PRICES[0] + cached * PRICES[1] + output_tokens * PRICES[2]) / 1_000_000


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class EvalBudget:
    usd: Decimal
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_seconds: float
    calls: int = 0
    spent: Decimal = Decimal("0")
    held: Decimal = Decimal("0")
    exceeded: bool = False
    started: float = field(default_factory=lambda: monotonic())

    def remaining_seconds(self) -> float:
        remaining = self.max_seconds - (monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("eval_deadline")
        return remaining

    def reserve(self, model: str, text: str, instructions: str, schema: dict, output_tokens: int) -> tuple[int, Decimal]:
        self.remaining_seconds()
        if model != MODEL or self.exceeded:
            raise BudgetExceeded("eval_model_or_usage_mismatch")
        if self.calls >= self.max_calls:
            raise BudgetExceeded("eval_call_limit")
        # Count bytes of the known request content conservatively and reserve additional provider framing.
        payload = json.dumps([text, instructions, schema], ensure_ascii=False, separators=(",", ":"))
        estimated = len(payload.encode("utf-8")) + FRAMING_ALLOWANCE
        if estimated > self.max_input_tokens or output_tokens > self.max_output_tokens:
            raise BudgetExceeded("eval_token_limit")
        reservation = price(estimated, output_tokens)
        if self.spent + self.held + reservation > self.usd:
            raise BudgetExceeded("budget_exhausted")
        self.calls += 1
        self.held += reservation
        return estimated, reservation

    def settle(self, reservation: tuple[int, Decimal], usage, output_limit: int) -> None:
        if usage is None:
            return
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None) or 0
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if (any(type(value) is not int for value in (input_tokens, output_tokens, cached))
                or not 0 <= cached <= input_tokens or output_tokens < 0):
            self.exceeded = True
            raise BudgetExceeded("eval_model_or_usage_mismatch")
        self.held -= reservation[1]
        self.spent += price(input_tokens, output_tokens, cached)
        if input_tokens > reservation[0] or output_tokens > output_limit:
            self.exceeded = True
            raise BudgetExceeded("eval_model_or_usage_mismatch")

    def report(self) -> dict:
        return {
            "budget_usd": str(self.usd), "max_calls": self.max_calls, "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens, "max_seconds": self.max_seconds,
            "price_per_million_usd": dict(zip(("input", "cached_input", "output"), map(str, PRICES), strict=True)),
            "pricing_date": PRICING_DATE, "pricing_url": PRICING_URL, "judge_enabled": False, "retries": 0,
            "calls": self.calls, "observed_usd": str(self.spent), "held_for_unknown_usd": str(self.held),
            "total_usd": str(self.spent) if not self.held else None,
        }
