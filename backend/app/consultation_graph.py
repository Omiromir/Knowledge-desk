"""One consultation workflow, paused at a durable human approval boundary."""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import TypedDict
from weakref import WeakValueDictionary

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection, Error as PostgresError
from psycopg.rows import dict_row
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from . import consultation_store as store, database, postgres
from .config import CONSULTATION_ACTIVE_SECONDS, CONSULTATION_MAX_STEPS, CONSULTATION_MAX_TOOL_CALLS
from .tool_execution import RequestDraft, SearchArguments, ToolContext, ToolError, execute_tool

logger = logging.getLogger("uvicorn.error.knowledge_desk.consultations")
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class ConsultationState(TypedDict, total=False):
    user_id: str
    draft_id: str
    version: int
    name: str
    question: str
    sources: list[dict]
    status: str
    confirmation: dict | None
    request_id: str | None
    stop_code: str | None
    steps: int
    tool_calls: int


@dataclass(frozen=True)
class ActiveRun:
    tools: ToolContext
    deadline: float


class StopRun(Exception):
    def __init__(self, code: str):
        self.code = code


def checkpoint_file():
    return database.DB_PATH.with_name(database.DB_PATH.stem + ".consultations.sqlite3")


@asynccontextmanager
async def checkpoints():
    if url := postgres.database_url():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with await AsyncConnection.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=0) as connection:
            await connection.execute("SET search_path TO knowledge_desk, pg_catalog")
            yield AsyncPostgresSaver(connection)
        return
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_file())) as saver:
        yield saver


def graph_config(row: dict) -> dict:
    return {"configurable": {"thread_id": store.operation_key(row["draft_id"], row["version"])},
            "recursion_limit": CONSULTATION_MAX_STEPS + 2}


def draft_lock(draft_id: str) -> asyncio.Lock:
    # shortcut: serialize LangGraph writes within the local server process; SQLite arbitrates request creation.
    return _locks.setdefault(draft_id, asyncio.Lock())


async def tool_data(name: str, args, context: ToolContext) -> dict:
    result = (await execute_tool(name, args.model_dump_json(), context, allow_retry=False)).result
    if isinstance(result, ToolError):
        raise StopRun(result.code)
    return result.data.model_dump()


async def search(state: ConsultationState, run: ActiveRun) -> tuple[dict, str]:
    data = await tool_data("search_documents", SearchArguments(query=state["question"][:1200], limit=3), run.tools)
    return {"sources": data["fragments"]}, "prepare"


async def prepare(state: ConsultationState, run: ActiveRun) -> tuple[dict, str]:
    draft = RequestDraft(name=state["name"], question=state["question"])
    data = await tool_data("prepare_request", draft, run.tools)
    if data != draft.model_dump():
        raise StopRun("draft_changed_by_tool")
    return data, "preview"


async def preview(state: ConsultationState, run: ActiveRun) -> tuple[dict, str]:
    return {"status": "awaiting_confirmation"}, "review"


def confirmation_for(state: ConsultationState | dict) -> dict:
    return {"user_id": state["user_id"], "draft_id": state["draft_id"], "version": state["version"],
            "payload_hash": store.payload_hash(state["name"], state["question"])}


async def review(state: ConsultationState, run: ActiveRun) -> tuple[dict, str]:
    confirmation = interrupt({key: state[key] for key in ("draft_id", "version", "name", "question")})
    if confirmation != confirmation_for(state):
        raise StopRun("invalid_confirmation")
    return {"confirmation": confirmation}, "submit"


async def submit(state: ConsultationState, run: ActiveRun) -> tuple[dict, str]:
    approval = state.get("confirmation")
    if approval is None or approval != confirmation_for(state):
        raise StopRun("invalid_confirmation")
    request_id = store.submit_confirmed(approval["user_id"], approval["draft_id"], approval["version"], approval["payload_hash"])
    return {"status": "submitted", "request_id": request_id}, END


def guarded(node, *, uses_tool: bool):
    async def run(state: ConsultationState, runtime: Runtime[ActiveRun]) -> Command:
        context = runtime.context
        steps, calls = state["steps"], state["tool_calls"]
        row = store.get_draft(state["user_id"], state["draft_id"])
        store.check_version(row, state["version"])
        if state["user_id"] != context.tools.user_id:
            raise store.DraftConflict("invalid_confirmation")
        if row["status"] == "cancelled":
            return Command(update={"status": "cancelled", "stop_code": "cancelled"}, goto=END)
        if row["status"] == "submitted":
            return Command(update={"status": "submitted", "request_id": row["request_id"]}, goto=END)
        if row["status"] == "failed":
            return Command(update={"status": "failed", "stop_code": row["stop_code"]}, goto=END)

        code = None
        if monotonic() >= context.deadline:
            code = "active_timeout"
        elif steps >= CONSULTATION_MAX_STEPS:
            code = "step_limit"
        elif uses_tool and calls >= CONSULTATION_MAX_TOOL_CALLS:
            code = "tool_limit"
        if code:
            return Command(update={"status": "failed", "stop_code": code}, goto=END)
        try:
            update, next_node = await node(state, context)
        except StopRun as stop:
            return Command(update={"status": "failed", "stop_code": stop.code,
                                   "steps": steps + 1, "tool_calls": calls + int(uses_tool)}, goto=END)
        return Command(update={**update, "steps": steps + 1, "tool_calls": calls + int(uses_tool)}, goto=next_node)
    return run


def compile_graph(saver):
    graph = StateGraph(ConsultationState, context_schema=ActiveRun)
    graph.add_edge(START, "search")
    for name, node, uses_tool in (("search", search, True), ("prepare", prepare, True), ("preview", preview, False),
                                  ("review", review, False), ("submit", submit, False)):
        graph.add_node(name, guarded(node, uses_tool=uses_tool))
    return graph.compile(checkpointer=saver)


def view(row: dict, state: dict | None = None) -> dict:
    return {key: row[key] for key in ("draft_id", "version", "name", "question", "status", "stop_code", "request_id")} | {
        "sources": (state or {}).get("sources", []),
    }


async def read_draft(user_id: str, draft_id: str) -> dict:
    row = store.get_draft(user_id, draft_id)
    if not postgres.database_url() and not checkpoint_file().is_file():
        return view(row)
    async with checkpoints() as saver:
        checkpoint = await compile_graph(saver).aget_state(graph_config(row))
        return view(row, checkpoint.values)


async def advance(graph, row: dict, context: ToolContext, input_state) -> dict:
    config = graph_config(row)
    run = ActiveRun(context, monotonic() + CONSULTATION_ACTIVE_SECONDS)
    try:
        async with asyncio.timeout(CONSULTATION_ACTIVE_SECONDS):
            result = await graph.ainvoke(input_state, config, context=run, durability="sync")
        if not isinstance(result, dict):
            result = result.value
    except TimeoutError:
        result = {"status": "failed", "stop_code": "active_timeout"}
    except (sqlite3.Error, PostgresError) as error:
        logger.warning("Consultation storage failure type=%s", type(error).__name__)
        result = {"status": "failed", "stop_code": "storage_failed"}

    key = row["user_id"], row["draft_id"], row["version"]
    if result.get("status") == "awaiting_confirmation" and result.get("__interrupt__"):
        row = store.set_outcome(*key, "awaiting_confirmation")
    elif result.get("status") in {"submitted", "cancelled"}:
        row = store.get_draft(*key[:2])
    else:
        code = result.get("stop_code") or "workflow_failed"
        row = store.set_outcome(*key, "failed", code)
        if row["status"] == "failed":
            logger.info("Consultation stopped draft=%s version=%d code=%s", row["draft_id"], row["version"], code)
        elif row["status"] == "submitted":
            logger.info("Consultation recovered committed submission draft=%s version=%d", row["draft_id"], row["version"])
    checkpoint = await graph.aget_state(config)
    return view(row, checkpoint.values)


async def start_preparation(context: ToolContext, user_id: str, draft_id: str, version: int) -> dict:
    async with draft_lock(draft_id):
        row = store.get_draft(user_id, draft_id)
        store.check_version(row, version)
        if row["status"] != "draft":
            return await read_draft(user_id, draft_id)
        async with checkpoints() as saver:
            graph = compile_graph(saver)
            snapshot = await graph.aget_state(graph_config(row))
            if any(task.interrupts for task in snapshot.tasks):
                return view(store.set_outcome(user_id, draft_id, version, "awaiting_confirmation"), snapshot.values)
            if snapshot.values:
                # A crashed tool may have started before its counter reached the next checkpoint.
                code = snapshot.values.get("stop_code") or "preparation_interrupted"
                logger.info("Consultation stopped draft=%s version=%d code=%s", draft_id, version, code)
                return view(store.set_outcome(user_id, draft_id, version, "failed", code), snapshot.values)
            initial = {**row, "steps": 0, "tool_calls": 0, "sources": [], "confirmation": None}
            return await advance(graph, row, context, initial)


async def confirm_draft(context: ToolContext, user_id: str, draft_id: str, version: int, data: RequestDraft) -> dict:
    digest = store.payload_hash(data.name, data.question)
    async with draft_lock(draft_id):
        row = store.confirmation_status(user_id, draft_id, version, digest)
        if row["request_id"]:
            return await read_draft(user_id, draft_id)
        async with checkpoints() as saver:
            graph = compile_graph(saver)
            snapshot = await graph.aget_state(graph_config(row))
            approval = confirmation_for(row)
            if any(task.interrupts for task in snapshot.tasks):
                return await advance(graph, row, context, Command(resume=approval))
            if snapshot.next == ("submit",) and snapshot.values.get("confirmation") == approval:
                return await advance(graph, row, context, None)
            return view(store.set_outcome(user_id, draft_id, version, "failed", "checkpoint_unavailable"), snapshot.values)


async def cancel_draft(user_id: str, draft_id: str, version: int) -> dict:
    row = store.set_outcome(user_id, draft_id, version, "cancelled", "cancelled")
    if row["status"] != "cancelled":
        raise store.DraftConflict("draft_closed")
    logger.info("Consultation cancelled draft=%s version=%d", draft_id, version)
    return await read_draft(user_id, draft_id)
