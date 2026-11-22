"""Allowlisted stage metadata correlated by a server-generated request ID."""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter

logger = logging.getLogger("uvicorn.error.knowledge_desk.trace")
trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


@contextmanager
def stage(name: str, **fields):
    current = trace_id.get()
    if current is None:
        yield fields
        return
    start = perf_counter()
    started_at = datetime.now(UTC).isoformat()
    try:
        yield fields
    except BaseException as exc:
        fields.update(status="error", error_type=type(exc).__name__)
        raise
    finally:
        logger.info(json.dumps({
            "trace_id": current, "stage": name, "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(), "duration_ms": round((perf_counter() - start) * 1000, 2),
            "status": "ok", **fields,
        }, separators=(",", ":")))


def usage_fields(response) -> dict:
    model = getattr(response, "model", None)
    provider_model = (model if isinstance(model, str) and model.isascii() and len(model) <= 80
                      and model.replace("-", "").replace(".", "").replace("_", "").isalnum() else None)
    usage = getattr(response, "usage", None)
    values = {
        "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None),
        "cached_input_tokens": getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None),
    }
    return {"provider_model": provider_model, **{key: value if type(value) is int else None for key, value in values.items()}}
