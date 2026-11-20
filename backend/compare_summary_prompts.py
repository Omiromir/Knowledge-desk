"""Run the fixed 20-call summary experiment once. Output excludes credentials."""

import asyncio
import json
import os
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from openai import OpenAIError

from .app.summarizer import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    PROMPT_A,
    PROMPT_B,
    TIMEOUT_SECONDS,
    SummaryError,
    request_summary_response,
    validate_summary_response,
)

DATA = Path(__file__).resolve().parents[1] / "data"


async def main():
    key = os.environ["OPENAI_API_KEY"]
    cases = json.loads((DATA / "summary-cases.json").read_text(encoding="utf-8"))
    if len(cases) != 10:
        raise ValueError("The experiment requires exactly ten cases.")
    prompts = {"a": PROMPT_A, "b": PROMPT_B}
    paths = {variant: DATA / f"summary-run-{variant}.jsonl" for variant in prompts}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Existing experiment data must be moved before running again.")

    with ExitStack() as stack:
        files = {variant: stack.enter_context(path.open("x", encoding="utf-8")) for variant, path in paths.items()}
        for variant, file in files.items():
            settings = {
                "model": MODEL, "sdk": version("openai"), "instructions": prompts[variant],
                "max_output_tokens": MAX_OUTPUT_TOKENS, "timeout_seconds": TIMEOUT_SECONDS,
                "max_retries": 0, "store": False, "structured_outputs": True,
            }
            file.write(json.dumps({"settings": settings}, ensure_ascii=False) + "\n")

        for index, case in enumerate(cases):
            order = ("a", "b") if index % 2 == 0 else ("b", "a")
            for variant in order:
                started = perf_counter()
                entry = dict.fromkeys(("model_returned", "raw_output", "usage", "type_correct", "error"))
                entry.update(id=case["id"], schema_valid=False)
                try:
                    response = await request_summary_response(case["text"], prompts[variant], key)
                    entry["duration_ms"] = round((perf_counter() - started) * 1000)
                    entry["model_returned"] = response.model
                    entry["raw_output"] = [item.model_dump(mode="json") for item in response.output]
                    entry["usage"] = response.usage.model_dump(mode="json") if response.usage else None
                    result = validate_summary_response(response)
                    entry.update(schema_valid=True, type_correct=result.request_type == case["expected_type"])
                except (OpenAIError, SummaryError, TimeoutError, ValueError) as exc:
                    entry["error"] = getattr(exc, "code", None) or type(exc).__name__
                entry.setdefault("duration_ms", round((perf_counter() - started) * 1000))
                files[variant].write(json.dumps(entry, ensure_ascii=False) + "\n")
                files[variant].flush()
                print(variant.upper(), case["id"], entry["duration_ms"], entry["error"] or "ok", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
