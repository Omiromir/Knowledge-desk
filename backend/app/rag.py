"""Grounded answers over the configured local corpus."""

import asyncio
import json
import logging
import math
import os
import re
from functools import lru_cache
from hashlib import sha256
from struct import Struct
from threading import BoundedSemaphore
from typing import Annotated

from pydantic import BaseModel, PositiveInt, StringConstraints, field_validator
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from backend.rag_index.ingest import DIMENSION, INDEX, MANIFEST, MODEL, REVISION, TARGET, embed, load_corpus, normalize, open_collection
from backend.rag_index.retrieve import TOP_K, resolve_chunk, search

from . import postgres, uploads, vector_index
from .storage import documents as document_store
from .summarizer import TIMEOUT_SECONDS, SummaryError, complete_structured
from .telemetry import stage
from .prompts import read_prompt

logger = logging.getLogger(__name__)
MAX_QUESTION_LENGTH = 1200
MAX_DISTANCE = float(os.environ.get("KNOWLEDGE_DESK_MAX_DISTANCE", "0.18"))
if not 0 <= MAX_DISTANCE <= 2:
    raise ValueError("KNOWLEDGE_DESK_MAX_DISTANCE must be between 0 and 2.")
MAX_ANSWER_TOKENS = 512
MAX_ASK_ATTEMPTS = 20
_attempts = BoundedSemaphore(MAX_ASK_ATTEMPTS)
QUERY_VECTOR = Struct(f"!{DIMENSION}f")
QUERY_VECTOR_TTL = 600

PROMPT_VERSION = "document-grounded-v16"
ANSWER_POLICY_VERSION = "cosine-explicit-year-v4"
YEAR = r"\b(?:19|20)[0-9]{2}\b"
NAMED_YEAR = re.compile(rf"({YEAR})\s*(?:год(?:а|у|е|ом)?\b|г\.)", re.IGNORECASE)
# shortcut: recognizes direct login-password requests in Russian; extend with evaluated paraphrases.
LOGIN_PASSWORD_REQUEST = re.compile(
    r"\b(?:какой|каков|назовите|укажите|сообщите)\s+(?:у\s+(?:не[её]|него|них)\s+)?пароль\s+для\s+входа\b",
    re.IGNORECASE,
)
DOCUMENT_GROUNDED_V2 = read_prompt("rag/document-grounded-v2.txt")
DOCUMENT_GROUNDED_V3 = read_prompt("rag/document-grounded-v3.txt")
DOCUMENT_GROUNDED_V4 = read_prompt("rag/document-grounded-v4.txt")
DOCUMENT_GROUNDED_V5 = read_prompt("rag/document-grounded-v5.txt")
DOCUMENT_GROUNDED_V11 = read_prompt("rag/document-grounded-v11.txt")
SHARE_PROMPT_VERSION = "document-grounded-v11"
REQUIRED_SHARE = re.compile(
    r"\b(?:дол[яиюе]|процент(?:а|ов)?|share|percentage|proportion)\b.*?"
    r"\b(?:нужно|надо|необходимо|требуется|must|required|need(?:s)?)\b", re.IGNORECASE | re.DOTALL,
)
REQUIRED_SHARE_INSTRUCTIONS = read_prompt("rag/required-share.txt")
EVIDENCE_PROMPT_VERSION = "document-grounded-v12"
SCOPED_EVIDENCE_PROMPT_VERSION = "document-grounded-v13"
CONDITIONAL_EVIDENCE_PROMPT_VERSION = "document-grounded-v14"
VERIFIED_EVIDENCE_PROMPT_VERSION = "document-grounded-v15"
EXPLICIT_ACTOR_PROMPT_VERSION = "document-grounded-v16"
ACTOR_INSTRUCTIONS = read_prompt("rag/actor-v16.txt")
ANSWER_SCOPE_INSTRUCTIONS = read_prompt("rag/answer-scope.txt")
CONDITION_INSTRUCTIONS = read_prompt("rag/conditions.txt")
EVIDENCE_SCOPE = {
    EVIDENCE_PROMPT_VERSION: "",
    SCOPED_EVIDENCE_PROMPT_VERSION: ANSWER_SCOPE_INSTRUCTIONS,
    CONDITIONAL_EVIDENCE_PROMPT_VERSION: CONDITION_INSTRUCTIONS,
    VERIFIED_EVIDENCE_PROMPT_VERSION: CONDITION_INSTRUCTIONS,
    EXPLICIT_ACTOR_PROMPT_VERSION: ACTOR_INSTRUCTIONS + CONDITION_INSTRUCTIONS,
}
EVIDENCE_INSTRUCTIONS = read_prompt("rag/evidence.txt")
DOCUMENT_GROUNDED_V12 = EVIDENCE_INSTRUCTIONS + DOCUMENT_GROUNDED_V11
DOCUMENT_GROUNDED_V13 = ANSWER_SCOPE_INSTRUCTIONS + DOCUMENT_GROUNDED_V12
DOCUMENT_GROUNDED_V14 = CONDITION_INSTRUCTIONS + DOCUMENT_GROUNDED_V12
DOCUMENT_GROUNDED_V16 = ACTOR_INSTRUCTIONS + DOCUMENT_GROUNDED_V14
VERIFY_ANSWER_INSTRUCTIONS = read_prompt("rag/verify.txt")
ANSWER_VERIFICATION_PROMPTS = {
    VERIFIED_EVIDENCE_PROMPT_VERSION: VERIFY_ANSWER_INSTRUCTIONS,
    EXPLICIT_ACTOR_PROMPT_VERSION: ACTOR_INSTRUCTIONS + VERIFY_ANSWER_INSTRUCTIONS,
}
NUMBER_LITERAL = re.compile(
    r"(?:(?<!\d)[+−-])?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+)"
    r"(?:[.,:/]\d+)*(?:[ \t\u00a0\u202f]*%)?"
)
QUOTE_ELISION = re.compile(r"\s*(?:\.{3}|…)\s*")
NUMBER_REQUEST = re.compile(r"\b(?:по\s+какому\s+номеру|какой\s+(?:точный\s+)?номер)\b", re.IGNORECASE)
LINK_REQUEST = re.compile(r"\b(?:по\s+какой\s+(?:точной\s+)?ссылке|какая\s+(?:точная\s+)?ссылка|какой\s+url)\b", re.IGNORECASE)
URL_LITERAL = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
INCOMPLETE_EVIDENCE = "В найденных фрагментах недостаточно сведений для полного ответа."
INSTRUCTIONS = DOCUMENT_GROUNDED_V16


def instructions_for_question(question: str, instructions: str, prompt_version: str) -> str:
    if prompt_version in EVIDENCE_SCOPE and REQUIRED_SHARE.search(question):
        return EVIDENCE_SCOPE[prompt_version] + EVIDENCE_INSTRUCTIONS + REQUIRED_SHARE_INSTRUCTIONS
    if prompt_version == SHARE_PROMPT_VERSION and REQUIRED_SHARE.search(question):
        return REQUIRED_SHARE_INSTRUCTIONS
    return instructions


class AskInput(BaseModel):
    model_config = {"extra": "forbid"}
    question: Annotated[str, StringConstraints(min_length=1, max_length=MAX_QUESTION_LENGTH)]

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank.")
        return value.strip()


class AskDraft(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    answer: str
    answerable: bool
    citations: list[str]


class AnswerEvidence(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    question_part: str
    chunk_id: str | None
    quote: str | None


class AskEvidenceDraft(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    evidence: list[AnswerEvidence]
    answerable: bool
    answer: str
    citations: list[str]


class AnswerVerification(BaseModel):
    model_config = {"strict": True, "extra": "forbid"}
    missing_parts: list[str]
    unsupported_claims: list[str]


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    text: str
    source: str
    version_date: str
    page_number: PositiveInt | None = None


class AskResult(BaseModel):
    answer: str
    answerable: bool
    citations: list[Citation]


class SourceExcerpt(Citation):
    before: str
    after: str


def manifest_collection():
    if not INDEX.is_dir() or not MANIFEST.is_file():
        return None
    from chromadb.errors import NotFoundError

    try:
        return open_collection(INDEX, chunk_size=int(os.environ.get("KNOWLEDGE_DESK_CHUNK_SIZE", TARGET)))
    except NotFoundError:
        return None


def scoped_corpus(user_id: str | None, *, include_manifest: bool):
    catalog, versions, hidden = uploads.active_corpus()
    manifest = load_corpus() if include_manifest else {}
    if user_id is None:
        where = {"source": {"$nin": sorted(hidden)}} if hidden else None
        return catalog, versions, manifest, where

    readable = document_store.readable_documents(user_id)
    ids = {doc.id for doc in readable}
    materialized = {(doc.source, doc.title, normalize(doc.text)) for doc in readable}
    manifest = {key: doc for key, doc in manifest.items()
                if doc.source not in hidden and (doc.source, doc.title, doc.text) in materialized}
    where = {"doc_id": {"$in": sorted(manifest)}} if manifest else None
    return ({key: doc for key, doc in catalog.items() if key in ids},
            {key: value for key, value in versions.items() if key in ids}, manifest, where)


@lru_cache(maxsize=1)
def query_cache(url: str) -> Redis:
    return Redis.from_url(url, socket_connect_timeout=0.1, socket_timeout=0.1,
                          retry=Retry(NoBackoff(), 0))


def query_vector(question: str, user_id: str | None) -> list[float]:
    url = os.getenv("KNOWLEDGE_DESK_REDIS_URL")
    if not url:
        return embed([question], "query")[0]

    digest = sha256(f"{user_id or 'local'}\0{question}".encode()).hexdigest()
    key = f"knowledge-desk:e5-query:v1:{MODEL}:{REVISION}:{digest}"
    cache = None
    try:
        cache = query_cache(url)
        payload = cache.get(key)
        if isinstance(payload, bytes) and len(payload) == QUERY_VECTOR.size:
            vector = QUERY_VECTOR.unpack(payload)
            if all(map(math.isfinite, vector)):
                return list(vector)
    except (RedisError, ValueError):
        cache = None

    vector = embed([question], "query")[0]
    if cache is not None:
        try:
            cache.set(key, QUERY_VECTOR.pack(*vector), ex=QUERY_VECTOR_TTL)
        except RedisError:
            pass
    return vector


def retrieve_question(question: str, *, user_id: str | None = None) -> list[dict]:
    shared = bool(postgres.database_url())
    with stage("retrieval", revision=REVISION, index="pgvector" if shared else "chroma", top_k=TOP_K) as record:
        try:
            record["chunk_size"] = vector_index.profile()
            if shared:
                hits = vector_index.search(query_vector(question, user_id), user_id) if user_id else []
            else:
                manifest_index = manifest_collection()
                catalog, versions, manifest, where = scoped_corpus(user_id, include_manifest=manifest_index is not None)
                record.update(manifest_available=manifest_index is not None, upload_revisions=len(versions))
                hits = []
                if manifest or versions:
                    vectors = [query_vector(question, user_id)]
                    if manifest and manifest_index is not None:
                        hits.extend(search(manifest_index, [question], manifest, vectors=vectors, where=where)[0])
                    if versions:
                        hits.extend(search(
                            uploads.collection(), [question], catalog, vectors=vectors,
                            where={"index_key": {"$in": list(versions.values())}},
                        )[0])
                    hits = sorted(hits, key=lambda hit: hit["distance"])[:TOP_K]
            record.update(hits=len(hits), status="ok" if hits else "empty")
            return hits
        except Exception as exc:
            record["cause"] = type(exc).__name__
            raise SummaryError("index_unavailable", "The search index is unavailable.") from None


def get_source(chunk_id: str, *, user_id: str | None = None) -> SourceExcerpt:
    try:
        if postgres.database_url():
            if not user_id:
                raise LookupError(chunk_id)
            row = vector_index.source(chunk_id, user_id)
        else:
            row = chroma_source(chunk_id, user_id)
        document = normalize(row["document_text"])
        if document[row["start"]:row["end"]] != row["text"]:
            raise LookupError(chunk_id)
    except LookupError:
        raise SummaryError("source_not_found", "This source fragment was not found.") from None
    except Exception as exc:
        logger.error("RAG source lookup failed: %s", type(exc).__name__)
        raise SummaryError("index_unavailable", "The search index is unavailable.") from None
    return SourceExcerpt(
        **Citation.model_validate(row).model_dump(),
        before=document[:row["start"]], after=document[row["end"]:],
    )


def chroma_source(chunk_id: str, user_id: str | None) -> dict:
    uploaded = chunk_id.startswith(uploads.CHUNK_PREFIX)
    index = None if uploaded else manifest_collection()
    catalog, versions, manifest, where = scoped_corpus(user_id, include_manifest=index is not None)
    if uploaded and versions:
        return resolve_chunk(uploads.collection(), chunk_id, catalog, where={"index_key": {"$in": list(versions.values())}})
    if index is not None and manifest:
        return resolve_chunk(index, chunk_id, manifest, where=where)
    raise LookupError(chunk_id)


def select_context(hits: list[dict], *, max_distance: float = MAX_DISTANCE) -> list[Citation]:
    with stage("context", hits=len(hits), max_distance=max_distance) as record:
        selected = [Citation.model_validate(hit) for hit in hits[:TOP_K] if hit["distance"] <= max_distance]
        record["selected"] = len(selected)
        return selected


def abstention_reason(question: str, context: list[Citation]) -> str | None:
    if LOGIN_PASSWORD_REQUEST.search(question):
        return "Значение пароля для входа через «Ответы» недоступно. За данными для входа обратитесь к владельцу системы."
    if not context:
        return "В документах не нашлось достаточно близких фрагментов, чтобы ответить на этот вопрос."
    years = set(NAMED_YEAR.findall(question))
    evidence = "\n".join(f"{row.version_date}\n{row.text}" for row in context)
    if years and not years <= set(re.findall(YEAR, evidence)):
        return "В найденных документах нет сведений за указанный год."
    return None


async def answer_question(question: str, context: list[Citation]) -> AskResult:
    with stage("grounding", version=PROMPT_VERSION, policy=ANSWER_POLICY_VERSION, context_count=len(context)) as record:
        reason = abstention_reason(question, context)
        if reason:
            record.update(status="refused", code="no_context" if not context else "policy")
            return AskResult(answer=reason, answerable=False, citations=[])
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise SummaryError("not_configured", "The answer model is not configured.")
        # shortcut: process-local cap for this local pilot; use a shared quota before publishing.
        if not _attempts.acquire(blocking=False):
            raise SummaryError("local_limit", "Local answer limit reached.")
        result = await generate_answer(question, context, api_key)
        record.update(status="ok" if result.answerable else "refused", citations=len(result.citations))
        return result


async def generate_answer(question: str, context: list[Citation], api_key: str, *, instructions: str = INSTRUCTIONS,
                          prompt_version: str = PROMPT_VERSION, on_verification_response=None, **provider_options) -> AskResult:
    payload = json.dumps({
        "question": question,
        "fragments": [row.model_dump(exclude={"doc_id", "source", "page_number"}) for row in context],
    }, ensure_ascii=False)
    instructions = instructions_for_question(question, instructions, prompt_version)
    schema = AskEvidenceDraft if prompt_version in EVIDENCE_SCOPE else AskDraft
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            draft = await complete_structured(payload, instructions, api_key, schema, MAX_ANSWER_TOKENS, "invalid_answer", **provider_options)
            result = validate_answer(draft, context)
            verification_instructions = ANSWER_VERIFICATION_PROMPTS.get(prompt_version)
            if verification_instructions and result.answerable:
                if not provides_requested_value(question, result):
                    return AskResult(answer=INCOMPLETE_EVIDENCE, answerable=False, citations=[])
                verification_payload = json.dumps({
                    "question": question,
                    "answer": result.answer,
                    "sources": [{"title": source.title, "text": source.text} for source in result.citations],
                }, ensure_ascii=False)
                verification_options = {**provider_options, "on_response": on_verification_response}
                verification = await complete_structured(
                    verification_payload, verification_instructions, api_key, AnswerVerification,
                    MAX_ANSWER_TOKENS, "invalid_answer_verification", **verification_options,
                )
                if verification.missing_parts or verification.unsupported_claims:
                    return AskResult(answer=INCOMPLETE_EVIDENCE, answerable=False, citations=[])
            return result
    except TimeoutError:
        raise SummaryError("provider_timeout", f"The provider did not respond within {TIMEOUT_SECONDS} seconds.") from None


def provides_requested_value(question: str, result: AskResult) -> bool:
    if NUMBER_REQUEST.search(question) and not numeric_literals(result.answer):
        return False
    if LINK_REQUEST.search(question):
        links = {link.rstrip(".,;!?") for link in URL_LITERAL.findall(result.answer)}
        source_links = {
            link.rstrip(".,;!?") for row in result.citations for link in URL_LITERAL.findall(row.text)
        }
        return bool(links) and links <= source_links
    return True


def validate_answer(draft: AskDraft | AskEvidenceDraft, context: list[Citation]) -> AskResult:
    by_id = {row.chunk_id: row for row in context}
    if isinstance(draft, AskEvidenceDraft) and (
        not draft.answerable or any(item.chunk_id is None or item.quote is None for item in draft.evidence)
    ):
        return AskResult(answer=INCOMPLETE_EVIDENCE, answerable=False, citations=[])
    if (
        len(draft.citations) != len(set(draft.citations))
        or not set(draft.citations) <= by_id.keys()
        or draft.answerable != bool(draft.citations)
    ):
        raise SummaryError("invalid_citation", "The answer did not cite only valid supplied sources.")
    answer = draft.answer.strip()
    if not 1 <= len(answer) <= 1600:
        raise SummaryError("invalid_answer", "The answer was empty or too long.")
    if isinstance(draft, AskEvidenceDraft) and not evidence_supports_answer(answer, draft, by_id):
        return AskResult(answer=INCOMPLETE_EVIDENCE, answerable=False, citations=[])
    return AskResult(answer=answer, answerable=draft.answerable, citations=[by_id[key] for key in draft.citations])


def evidence_supports_answer(answer: str, draft: AskEvidenceDraft, by_id: dict[str, Citation]) -> bool:
    if not draft.evidence or {item.chunk_id for item in draft.evidence} != set(draft.citations):
        return False
    source_numbers = set()
    for item in draft.evidence:
        quote = " ".join((item.quote or "").split())
        fragment = by_id.get(item.chunk_id or "")
        if not fragment or not item.question_part.strip() or not quote_in_fragment(quote, fragment.text):
            return False
        fragment_numbers = numeric_literals(fragment.text)
        if not numeric_literals(quote) <= fragment_numbers:
            return False
        source_numbers.update(fragment_numbers)
    return numeric_literals(answer) <= source_numbers


def numeric_literals(text: str) -> set[str]:
    return {"".join(match.split()).replace("−", "-") for match in NUMBER_LITERAL.findall(text)}


def quote_in_fragment(quote: str, text: str) -> bool:
    if not quote:
        return False
    text = " ".join(text.split())
    number_spans = [match.span() for match in NUMBER_LITERAL.finditer(text)]
    offset = 0
    for part in QUOTE_ELISION.split(quote.strip(" .…")):
        part = part.rstrip(".!?,;: ")
        if not part:
            return False
        forms = (part, part[0].lower() + part[1:]) if part[0].isupper() else (part,)
        pattern = re.compile("|".join(map(re.escape, forms)))
        for match in pattern.finditer(text, offset):
            if any(start < match.start() < end or start < match.end() < end for start, end in number_spans):
                continue
            offset = match.end()
            break
        else:
            return False
    return True
