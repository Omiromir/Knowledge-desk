"""Compare a model judge with the frozen human labels for ten saved answers."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai.types.responses import Response
from pydantic import BaseModel

from backend.app.summarizer import SummaryError, complete_structured
from backend.app.prompts import read_prompt

MODEL = "gpt-4.1-2025-04-14"
RUBRIC = read_prompt("eval/judge-v1.txt")


class Verdict(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    grounded: bool | None
    complete: bool
    refusal: bool
    citations: bool
    note: str


async def compare(output: Path, api_key: str) -> None:
    root = Path("data/eval")
    human = json.loads((root / "judge-human.json").read_text(encoding="utf-8"))
    cases = json.loads((root / "checkpoint-b-results.json").read_text(encoding="utf-8"))["cases"]
    checks = ("grounded", "complete", "refusal", "citations")
    samples = []
    for case in cases:
        if case["id"] not in human:
            continue
        context = [{"chunk_id": row["chunk_id"], "text": row["text"]} for row in case["retrieved"]
                   if row["chunk_id"] in case["sent_chunk_ids"]]
        result = case["result"]
        payload = {
            "question": case["question"], "context": context,
            "answer": {"text": result["answer"], "answerable": result["answerable"],
                       "citations": [row["chunk_id"] for row in result["citations"]]},
        }
        samples.append((case, payload))
    assert len(samples) == len(human) == 10
    semaphore = asyncio.Semaphore(3)

    async def score(case, payload, variant="original"):
        usage = None

        def capture(response: Response) -> None:
            nonlocal usage
            usage = response.usage.model_dump() if response.usage else None

        row = {"id": case["id"], "variant": variant, "answer_sha256": case["result_sha256"],
               "human": human[case["id"]], "judge": None, "error": None}
        instructions = RUBRIC
        if variant == "reversed":
            payload = {**payload, "context": list(reversed(payload["context"]))}
            instructions = "Оцени каждый критерий отдельно. Порядок фрагментов не влияет на доказательность.\n" + RUBRIC
        async with semaphore:
            try:
                result = await complete_structured(
                    json.dumps(payload, ensure_ascii=False), instructions, api_key, Verdict, 300, "invalid_verdict",
                    model=MODEL, on_response=capture,
                )
                row["judge"] = result.model_dump()
            except SummaryError as exc:
                row["error"] = exc.code
        return {**row, "usage": usage}

    initial = await asyncio.gather(*(score(case, payload) for case, payload in samples))
    disputed = {row["id"] for row in initial if row["judge"] and
                any(row["judge"][key] != row["human"][key] for key in checks)}
    disputed.update({"R04", "B18"})
    reruns = await asyncio.gather(*(score(case, payload, "reversed") for case, payload in samples if case["id"] in disputed))
    output.write_text(json.dumps({"model": MODEL, "rubric": RUBRIC, "initial": initial, "reruns": reruns},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/eval/judge-comparison.json"))
    args = parser.parse_args()
    load_dotenv("backend/.env")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        parser.error("Set OPENAI_API_KEY on the server.")
    asyncio.run(compare(args.output, key))
