"""Evaluate the RAG pipeline on a labeled question set."""

import argparse
import asyncio
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from math import ceil, isfinite
from pathlib import Path
from time import perf_counter

from backend.rag_index.ingest import CHUNK_SIZES, INDEX, MANIFEST, ROOT, TARGET, embedding_model, ingest, load_corpus, open_collection
from backend.rag_index.retrieve import TOP_K, resolve_chunk, search, source_record

from .app.eval_budget import MODEL as EXPERIMENT_MODEL, PRICING_DATE, PRICING_URL, EvalBudget, price
from .app.prompts import read_prompt
from .app.rag import (
    ANSWER_POLICY_VERSION, DOCUMENT_GROUNDED_V2, DOCUMENT_GROUNDED_V3, DOCUMENT_GROUNDED_V4, DOCUMENT_GROUNDED_V5,
    ANSWER_VERIFICATION_PROMPTS, DOCUMENT_GROUNDED_V11, DOCUMENT_GROUNDED_V12, DOCUMENT_GROUNDED_V13,
    DOCUMENT_GROUNDED_V14, DOCUMENT_GROUNDED_V16, EXPLICIT_ACTOR_PROMPT_VERSION,
    CONDITIONAL_EVIDENCE_PROMPT_VERSION, EVIDENCE_PROMPT_VERSION, EVIDENCE_SCOPE, SCOPED_EVIDENCE_PROMPT_VERSION,
    INSTRUCTIONS, MAX_ANSWER_TOKENS, MAX_DISTANCE, PROMPT_VERSION, SHARE_PROMPT_VERSION,
    VERIFIED_EVIDENCE_PROMPT_VERSION,
    AskInput, AskResult, Citation, abstention_reason, generate_answer, instructions_for_question, select_context,
)
from .app.summarizer import TIMEOUT_SECONDS, SummaryError
from .eval_manifest import canonical, manifest, relative

DATA = ROOT / "data" / "eval"
DEV_CASES = DATA / "dev.json"
FULL_CASES = DATA / "checkpoint-b.json"
EXPERIMENT_PROMPTS = {
    "document-grounded-v2": DOCUMENT_GROUNDED_V2,
    "document-grounded-v3": DOCUMENT_GROUNDED_V3,
    "document-grounded-v4": DOCUMENT_GROUNDED_V4,
    "document-grounded-v5": DOCUMENT_GROUNDED_V5,
    **{f"document-grounded-v{revision}": read_prompt(f"rag/document-grounded-v{revision}.txt") for revision in range(6, 11)},
    SHARE_PROMPT_VERSION: DOCUMENT_GROUNDED_V11,
    EVIDENCE_PROMPT_VERSION: DOCUMENT_GROUNDED_V12,
    SCOPED_EVIDENCE_PROMPT_VERSION: DOCUMENT_GROUNDED_V13,
    CONDITIONAL_EVIDENCE_PROMPT_VERSION: DOCUMENT_GROUNDED_V14,
    VERIFIED_EVIDENCE_PROMPT_VERSION: DOCUMENT_GROUNDED_V14,
    EXPLICIT_ACTOR_PROMPT_VERSION: DOCUMENT_GROUNDED_V16,
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def result_digest(result: dict) -> str:
    return digest(json.dumps(result, ensure_ascii=False, sort_keys=True))


def apply_reviews(report: dict, reviews: dict) -> None:
    for case in report["cases"]:
        review = reviews.get(case["id"])
        case["manual_review"] = review if review and case["result_sha256"] == review["result_sha256"] else None


def summarize_report(cases: list[dict]) -> dict:
    positive = [case for case in cases if case["answerable"]]
    source_sets = [(set(case["expected_doc_ids"]), {hit["doc_id"] for hit in case["retrieved"]}) for case in positive]
    retrieval_ms = sorted(case["retrieval_ms"] for case in cases if case["retrieval_ms"] is not None)
    answer_ms = sorted(case["duration_ms"] for case in cases)
    return {
        "label_matches": sum(case["result"] is not None and case["result"]["answerable"] == case["answerable"] for case in cases),
        "manual_reviews": sum(case["manual_review"] is not None for case in cases),
        "manual_passes": sum(case["manual_review"] is not None and case["manual_review"]["grade"] == "pass" for case in cases),
        "provider_calls": sum(case["model_called"] for case in cases),
        "hit_at_5": sum(bool(expected & found) for expected, found in source_sets) / len(positive) if positive and retrieval_ms else None,
        "all_sources_at_5": sum(expected <= found for expected, found in source_sets) if retrieval_ms else None,
        "positive_cases": len(positive),
        "p95_retrieval_ms": retrieval_ms[ceil(len(retrieval_ms) * 0.95) - 1] if retrieval_ms else None,
        "p95_total_ms": answer_ms[ceil(len(answer_ms) * 0.95) - 1],
    }


async def evaluate(output: Path, index: Path = INDEX, chunk_size: int = TARGET, *,
                   budget: EvalBudget,
                   experiment: bool = False, build: bool = False, manual_context: Path | None = None,
                   experiment_prompt: str | None = None, cases_path: Path = DEV_CASES) -> bool:
    cases_bytes = cases_path.read_bytes()
    cases = json.loads(cases_bytes)
    using_dev = cases_path.resolve() == DEV_CASES
    corpus = load_corpus()
    question_count = len({AskInput(question=case["question"]).question for case in cases})
    if not cases or len({case["id"] for case in cases}) != len(cases) or question_count != len(cases):
        raise ValueError("Expected distinct evaluation case IDs and questions.")
    if using_dev and len(cases) != 20:
        raise ValueError("Expected 20 development cases.")
    for case in cases:
        expected = set(case["expected_doc_ids"])
        if bool(expected) != case["answerable"] or not expected <= corpus.keys() or not case["reference_answer"].strip():
            raise ValueError(f"Invalid label for {case['id']}.")
    abstentions = sum(not c["answerable"] for c in cases)
    minimum_abstentions = 5 if using_dev else 1
    if not minimum_abstentions <= abstentions < len(cases):
        raise ValueError("Expected answerable and unanswerable cases, with at least five unanswerable in the development set.")

    corpus_sha256 = digest(MANIFEST.read_text(encoding="utf-8") + json.dumps({k: d.text for k, d in corpus.items()}, sort_keys=True))
    historical = json.loads((DATA / "results.json").read_text(encoding="utf-8")) if experiment else None
    if historical:
        if historical["versions"]["corpus_sha256"] != corpus_sha256:
            raise ValueError("The experiment requires the original Orbita corpus.")
        if using_dev and any(
            any(case[key] != original[key] for key in ("id", "question", "answerable", "expected_doc_ids", "reference_answer"))
            for case, original in zip(cases, historical["cases"], strict=True)
        ):
            raise ValueError("The experiment requires the original development cases when using dev.json.")
    manual_ids = json.loads(manual_context.read_text(encoding="utf-8")) if manual_context else None
    if manual_ids is not None:
        cases = [case for case in cases if case["id"] in manual_ids and case["answerable"]]
        if len(cases) != 5 or len(manual_ids) != 5 or any(not 1 <= len(ids) <= TOP_K or len(ids) != len(set(ids)) for ids in manual_ids.values()):
            raise ValueError("Provide up to five distinct chunks for each of five answerable cases.")
    instructions = read_prompt(f"rag/{historical['versions']['prompt_version']}.txt") if historical else INSTRUCTIONS
    prompt_version = historical["versions"]["prompt_version"] if historical else PROMPT_VERSION
    if experiment_prompt is not None:
        instructions = EXPERIMENT_PROMPTS[experiment_prompt]
        prompt_version = experiment_prompt
    max_distance = historical["settings"]["max_cosine_distance"] if historical else MAX_DISTANCE
    reviews_path = output.with_suffix(".reviews.json") if experiment or not using_dev else DATA / "reviews.json"
    reviews = json.loads(reviews_path.read_text(encoding="utf-8")) if reviews_path.exists() else {}
    with output.open("x", encoding="utf-8") as file:
        if experiment and not manual_ids:
            embedding_model()  # Exclude one-time model loading from search and build timings.
        index_build = ingest(index, chunk_size=chunk_size) if build else None
        collection = open_collection(index, chunk_size=chunk_size)
        indexed = collection.get(include=["documents", "metadatas", "embeddings"])
        index_rows = sorted(zip(indexed["ids"], indexed["documents"], indexed["metadatas"],
                                indexed["embeddings"].tolist(), strict=True))
        if index_build is not None:
            repeat = ingest(index, chunk_size=chunk_size)
            if repeat["upserted"] or repeat["removed"] or repeat["chunks"] != index_build["chunks"]:
                raise RuntimeError("Rebuilding the same index changed its contents.")
            index_build["repeat"] = repeat
            index_build["bytes_on_disk"] = sum(path.stat().st_size for path in index.rglob("*") if path.is_file())
            rows = collection.get(include=["documents", "metadatas"])
            if len(set(rows["ids"])) != index_build["chunks"]:
                raise RuntimeError("Index contains duplicate chunk IDs.")
            for chunk_id, text, metadata in zip(rows["ids"], rows["documents"], rows["metadatas"], strict=True):
                source_record(chunk_id, text, metadata, corpus)
        report = {
            "versions": {
                "corpus_sha256": corpus_sha256,
                "dev_sha256" if using_dev else "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
                "index": {**collection.metadata, "chromadb": version("chromadb"),
                          "chunk_ids_sha256": digest("\n".join(row[0] for row in index_rows)),
                          "contents_sha256": digest(canonical(index_rows))},
                "embedding_runtime": {package: version(package) for package in ("sentence-transformers", "transformers", "torch")},
                "model": EXPERIMENT_MODEL, "sdk": version("openai"), "prompt_version": prompt_version,
                "answer_policy_version": ANSWER_POLICY_VERSION,
                "prompt_sha256": digest(instructions), "prompt": instructions,
            },
            "settings": {"top_k": TOP_K, "max_cosine_distance": max_distance, "max_output_tokens": MAX_ANSWER_TOKENS,
                         "provider_timeout_seconds": TIMEOUT_SECONDS, "provider_retries": 0},
            "index_build": index_build,
            "mode": "provider",
            "planned_cases": len(cases),
            "cases": [],
        }
        if not using_dev:
            report["versions"]["cases_file"] = cases_path.name
        if prompt_version == SHARE_PROMPT_VERSION or prompt_version in EVIDENCE_SCOPE:
            share_prompt = instructions_for_question("Какую долю нужно выполнить?", instructions, prompt_version)
            report["versions"]["required_share_prompt"] = share_prompt
            report["versions"]["required_share_prompt_sha256"] = digest(share_prompt)
        if prompt_version in ANSWER_VERIFICATION_PROMPTS:
            verifier = ANSWER_VERIFICATION_PROMPTS[prompt_version]
            report["versions"]["verification_prompt"] = verifier
            report["versions"]["verification_prompt_sha256"] = digest(verifier)
        effective = {
            name: report["versions"][key] for name, key in
            (("answer", "prompt"), ("required_share", "required_share_prompt"), ("verification", "verification_prompt"))
            if key in report["versions"]
        }
        dataset_version = "checkpoint-b-v1" if cases_path.resolve() == FULL_CASES else "dev-v1" if using_dev else "custom-v1"
        report["manifest"] = manifest(
            model=EXPERIMENT_MODEL, prompt_version=prompt_version, effective_prompts=effective, cases=cases_path,
            dataset_version=dataset_version, corpus=corpus,
            index={**report["versions"]["index"], "path": index.name},
            retrieval={**report["settings"], "manual_context": (
                {"path": relative(manual_context), "sha256": digest(canonical(manual_ids))} if manual_context else None
            ), "budget": {key: value for key, value in budget.report().items()
                          if key not in {"calls", "observed_usd", "held_for_unknown_usd", "total_usd"}}},
        )
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY in the protected environment.")
        for case in cases:
            question = AskInput(question=case["question"]).question
            start = perf_counter()
            if manual_ids:
                hits = [resolve_chunk(collection, chunk_id, corpus) for chunk_id in manual_ids[case["id"]]]
                for hit in hits:
                    del hit["document_text"]
                context = [Citation.model_validate(hit) for hit in hits]
                retrieval_ms = None
            else:
                hits = search(collection, [question], corpus)[0]
                retrieval_ms = round((perf_counter() - start) * 1000, 2)
                context = select_context(hits, max_distance=max_distance)
            result, error_code = None, None
            errors = []
            provider = {}
            verification_response = {}

            def capture_response(response):
                provider.update(model=response.model, output_text=response.output_text,
                                usage=response.usage.model_dump() if response.usage else None)
                if response.model != EXPERIMENT_MODEL:
                    raise SummaryError("model_mismatch", "Provider returned a different model snapshot.")

            def capture_verification(response):
                verification_response.update(model=response.model, output_text=response.output_text,
                                             usage=response.usage.model_dump() if response.usage else None)
                if response.model != EXPERIMENT_MODEL:
                    raise SummaryError("model_mismatch", "Provider returned a different model snapshot.")

            reason = abstention_reason(question, context)
            if not set(case["expected_doc_ids"]) <= {hit.doc_id for hit in context}:
                errors.append("source_not_found")
            generation_start = perf_counter()
            before = budget.calls
            try:
                if reason:
                    answer = AskResult(answer=reason, answerable=False, citations=[])
                else:
                    answer = await generate_answer(question, context, api_key, instructions=instructions,
                                                   prompt_version=prompt_version, model=EXPERIMENT_MODEL,
                                                   on_response=capture_response, on_verification_response=capture_verification,
                                                   budget=budget)
                result = answer.model_dump()
                if result["answerable"] != case["answerable"]:
                    errors.append("wrong_abstention")
            except SummaryError as exc:
                error_code = exc.code
                errors.append("invalid_citation" if exc.code == "invalid_citation" else "request_error")
            model_called = budget.calls > before
            report["cases"].append({
                **case, "retrieved": hits, "sent_chunk_ids": [hit.chunk_id for hit in context] if model_called else [],
                "selected_chunk_ids": [hit.chunk_id for hit in context],
                "model_called": model_called,
                "provider": provider or None,
                "verification_provider": verification_response or None,
                "effective_prompt_sha256": digest(instructions_for_question(question, instructions, prompt_version)) if model_called else None,
                "result": result, "error_code": error_code, "errors": errors,
                "retrieval_ms": retrieval_ms,
                "generation_ms": round((perf_counter() - generation_start) * 1000, 2) if model_called else None,
                "duration_ms": round((perf_counter() - start) * 1000),
                "result_sha256": result_digest(result) if result is not None else None,
            })
            print(case["id"], error_code or result["answerable"], flush=True)
            apply_reviews(report, reviews)
            report["summary"] = summarize_report(report["cases"])
            report["spend"] = budget.report()
            report["unprocessed_cases"] = [row["id"] for row in cases[len(report["cases"]):]]
            report["complete"] = not report["unprocessed_cases"] and all(not row["error_code"] for row in report["cases"])
            file.seek(0)
            file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            file.truncate()
            file.flush()
            if error_code in {"budget_exhausted", "eval_call_limit", "eval_deadline", "eval_model_or_usage_mismatch",
                              "provider_quota_exhausted", "model_mismatch"}:
                break
    return report["complete"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--chunk-size", type=int, choices=CHUNK_SIZES, default=TARGET)
    parser.add_argument("--cases", type=Path, default=FULL_CASES, help="Labeled question set. Defaults to 50 cases.")
    parser.add_argument("--experiment", action="store_true", help="Use the frozen Orbita evaluation prompt and model.")
    parser.add_argument("--experiment-prompt", choices=EXPERIMENT_PROMPTS, help="Override the frozen prompt for an A/B evaluation.")
    parser.add_argument("--build", action="store_true", help="Build the selected experiment index and measure the build.")
    parser.add_argument("--manual-context", type=Path, help="JSON mapping from five case IDs to hand-selected chunk IDs.")
    parser.add_argument("--budget-usd", default="0", help="Maximum reserved spend in USD.")
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--max-input-tokens", type=int, default=24000)
    parser.add_argument("--max-seconds", type=float, default=1800)
    parser.add_argument("--confirm-spend", action="store_true", help="Authorize provider calls within the printed budget.")
    args = parser.parse_args()
    if (args.build or args.manual_context or args.experiment_prompt) and not args.experiment:
        parser.error("--build, --manual-context and --experiment-prompt require --experiment")
    if args.build and (args.chunk_size == TARGET or args.index.resolve() == INDEX):
        parser.error("--build requires a separate 300- or 600-character index")
    try:
        amount = Decimal(args.budget_usd)
    except InvalidOperation:
        parser.error("--budget-usd must be a decimal amount.")
    if (not amount.is_finite() or amount < 0 or args.max_calls < 1 or args.max_input_tokens < 1
            or not isfinite(args.max_seconds) or args.max_seconds <= 0):
        parser.error("Provide finite, positive call, token and time limits and a nonnegative budget.")
    case_count = 5 if args.manual_context else len(json.loads(args.cases.read_text(encoding="utf-8")))
    prompt = args.experiment_prompt or ("orbita-grounded-v1" if args.experiment else PROMPT_VERSION)
    possible_calls = min(args.max_calls, case_count * (2 if prompt in ANSWER_VERIFICATION_PROMPTS else 1))
    ceiling = price(args.max_input_tokens, MAX_ANSWER_TOKENS) * possible_calls
    print(f"{EXPERIMENT_MODEL} | {case_count} cases | at most {possible_calls} calls | "
          f"{args.max_input_tokens} input / {MAX_ANSWER_TOKENS} output tokens per call | {args.max_seconds:g}s", flush=True)
    print(f"No retries or judge | Rate checked {PRICING_DATE}: {PRICING_URL} | "
          f"Upper estimate USD {ceiling} | Authorized budget USD {amount}", flush=True)
    if not args.confirm_spend:
        print("Dry run. No provider calls.")
        raise SystemExit(0)
    if amount == 0 or not args.output or not os.environ.get("OPENAI_API_KEY", "").strip():
        parser.error("Paid mode requires a positive budget, a fresh --output path, and OPENAI_API_KEY.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    budget = EvalBudget(amount, args.max_calls, args.max_input_tokens, MAX_ANSWER_TOKENS, args.max_seconds)
    complete = asyncio.run(evaluate(args.output, args.index, args.chunk_size, budget=budget, experiment=args.experiment,
                                    build=args.build, manual_context=args.manual_context,
                                    experiment_prompt=args.experiment_prompt, cases_path=args.cases))
    raise SystemExit(0 if complete else 1)
