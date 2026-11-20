import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..domain import NotFoundError
from ..consultation_store import DraftConflict

logger = logging.getLogger(__name__)


async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def draft_conflict(request: Request, exc: DraftConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": {"code": exc.code}})


async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for issue in exc.errors():
        entry = {key: issue[key] for key in ("type", "loc", "msg")}
        limits = {key: value for key, value in issue.get("ctx", {}).items() if type(value) in (int, float)}
        if limits:
            entry["ctx"] = limits
        errors.append(entry)
    return JSONResponse(status_code=422, content={"detail": errors})


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error for %s %s (%s)", request.method,
                 getattr(request.scope.get("route"), "path", "<unmatched>"), type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})
