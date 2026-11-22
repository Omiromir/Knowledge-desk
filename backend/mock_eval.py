"""Run five fixed evaluation cases without network or provider credentials."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from backend.rag_index.ingest import ROOT, chunk_document, index_metadata, load_corpus
from backend.rag_index.retrieve import TOP_K, source_record

from .app import database, rag, summarizer, tool_execution
from .app.eval_budget import MODEL
from .eval_manifest import canonical, digest, manifest

CASES = ROOT / "data/eval/mock-ci-v1.json"
PROFILE = 600
FACT = "Вводное занятие центра «Орбита» бесплатное и длится 30 минут."


def response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"object": "response", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload, ensure_ascii=False)}]},
    ]})


async def run_case(case: dict, context: list[rag.Citation]) -> dict:
    kind = case["kind"]
    calls = 0

    async def model(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if body["text"]["format"]["name"] == "AnswerVerification":
            return response({"missing_parts": [], "unsupported_claims": []})
        chunk = "invented-id" if kind == "invented" else context[0].chunk_id
        return response({
            "answer": FACT, "answerable": "true" if kind == "malformed" else True, "citations": [chunk],
            "evidence": [{"question_part": "Длительность", "chunk_id": chunk, "quote": FACT}],
        })

    def sdk(**settings):
        return AsyncOpenAI(**settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)))

    with patch.object(summarizer, "AsyncOpenAI", sdk):
        try:
            if kind == "action":
                user = tool_execution.ToolContext("local", frozenset(tool_execution.TOOLS))
                call = await tool_execution.execute_tool(
                    "submit_request", json.dumps({"name": "Тест", "question": case["question"]}), user,
                )
                actual = call.result.code if isinstance(call.result, tool_execution.ToolError) else "allowed"
            elif kind == "missing":
                answer = await rag.answer_question(case["question"], [])
                actual = "refused" if not answer.answerable and not answer.citations else "unfounded_answer"
            else:
                answer = await rag.generate_answer(case["question"], context, "mock-key", model=MODEL)
                actual = "grounded" if answer.answer == FACT and answer.citations == context else "wrong_answer"
        except summarizer.SummaryError as exc:
            actual = exc.code
    return {"id": case["id"], "expected": case["expected"], "actual": actual, "mock_calls": calls,
            "expected_calls": case["expected_calls"]}


async def run(output: Path, replay: Path | None) -> bool:
    dataset = json.loads(CASES.read_text(encoding="utf-8"))
    corpus = load_corpus()
    chunks = [row for doc in corpus.values() for row in chunk_document(doc, chunk_size=PROFILE)]
    chunk_id, text, metadata = next(row for row in chunks if FACT in row[1])
    hit = source_record(chunk_id, text, metadata, corpus)
    context = [rag.Citation.model_validate(hit)]
    prompts = {
        "answer": rag.INSTRUCTIONS,
        "required_share": rag.instructions_for_question("Какую долю нужно выполнить?", rag.INSTRUCTIONS, rag.PROMPT_VERSION),
        "verification": rag.ANSWER_VERIFICATION_PROMPTS[rag.PROMPT_VERSION],
    }
    inputs = manifest(model=MODEL, prompt_version=rag.PROMPT_VERSION, effective_prompts=prompts, cases=CASES,
                      dataset_version=dataset["version"], corpus=corpus,
                      index={**index_metadata(PROFILE), "kind": "local-fixture-chunks",
                             "contents_sha256": digest(canonical(sorted(chunks))),
                             "chunk_ids_sha256": digest("\n".join(sorted(row[0] for row in chunks)))},
                      retrieval={"top_k": TOP_K, "max_cosine_distance": rag.MAX_DISTANCE})
    previous = json.loads(replay.read_text(encoding="utf-8")) if replay else None
    if previous and previous["manifest"]["inputs_sha256"] != inputs["inputs_sha256"]:
        raise ValueError("The current inputs do not match the manifest to be replayed.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as file, TemporaryDirectory(dir=output.parent) as directory, \
            patch.object(database, "DB_PATH", Path(directory) / "mock.sqlite3"), \
            patch.dict("os.environ", {"KNOWLEDGE_DESK_DATABASE_URL": "", "OPENAI_API_KEY": ""}):
        database.init_db()
        results = [await run_case(case, context) for case in dataset["cases"]]
        with database.connect() as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("requests", "integration_events", "consultation_drafts")}
        success = (len(results) == 5 and not any(counts.values())
                   and all(row["actual"] == row["expected"] and row["mock_calls"] == row["expected_calls"] for row in results))
        if previous:
            success = success and previous["cases"] == results and previous["database_counts"] == counts
        report = {"mode": "mock", "network_calls": 0, "manifest": inputs, "cases": results,
                  "database_counts": counts, "passed": success}
        file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for row in results:
        print(f"{row['id']}  {row['actual']}")
    return success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.output, args.replay)) else 1)
