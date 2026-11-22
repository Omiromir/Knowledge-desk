import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, database, originals, postgres
from .api import admin, ai, consultations, documents, errors, health, profile, request_certificates, request_delivery, requests, resend_webhook
from .api import auth as auth_api
from .certificate_updates import CertificateUpdates
from .config import FRONTEND_ORIGIN, require_auth, validate_origin
from .consultation_store import DraftConflict
from .domain import NotFoundError
from .rate_limit import ExpensiveRequests
from .telemetry import stage, trace_id

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_origin()
    app.state.expensive_requests = ExpensiveRequests()
    protected = require_auth()
    auth_config = auth.settings()
    url = postgres.database_url()
    if (protected or url) and auth_config is None:
        raise RuntimeError("This mode requires Supabase authentication.")
    if protected and not url:
        raise RuntimeError("This mode requires a PostgreSQL database.")
    try:
        database.init_db()
    except Exception as exc:
        logger.error("Database initialization failed (%s).", type(exc).__name__)
        raise RuntimeError("Database initialization failed.") from None
    await run_in_threadpool(originals.retry_cleanup)
    updates = app.state.certificate_updates = CertificateUpdates()
    listener = asyncio.create_task(updates.listen(url)) if url else None
    try:
        yield
    finally:
        if listener is not None:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener


def create_app() -> FastAPI:
    application = FastAPI(title="Knowledge Desk API", lifespan=lifespan, docs_url=None if require_auth() else "/docs",
                          redoc_url=None if require_auth() else "/redoc", openapi_url=None if require_auth() else "/openapi.json")

    @application.middleware("http")
    async def trace_request(request: Request, call_next):
        request_id = uuid4().hex
        token = trace_id.set(request_id)
        try:
            with stage("http", method=request.method) as record:
                try:
                    origin = request.headers.get("origin")
                    configured = auth.settings() is not None
                    allowed = {FRONTEND_ORIGIN}
                    if not require_auth():
                        allowed.add(str(request.base_url).rstrip("/"))
                    if configured and request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin not in allowed:
                        response = JSONResponse({"detail": "Недопустимый источник запроса."}, status_code=403)
                    else:
                        response = await call_next(request)
                    if configured:
                        response.headers.setdefault("Cache-Control", "private, no-store")
                except Exception as exc:
                    record["error_type"] = type(exc).__name__
                    response = await errors.unexpected_error(request, exc)
                    response.headers["Cache-Control"] = "no-store"
                response.headers["X-Trace-ID"] = request_id
                record.update(route=getattr(request.scope.get("route"), "path", "<unmatched>"),
                              status=response.status_code)
                return response
        finally:
            trace_id.reset(token)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["X-Trace-ID"],
    )

    application.add_exception_handler(NotFoundError, errors.not_found)
    application.add_exception_handler(DraftConflict, errors.draft_conflict)
    application.add_exception_handler(RequestValidationError, errors.invalid_input)
    application.include_router(health.router)
    application.include_router(documents.router)
    # The certificate event stream has a static path beside the unrestricted request ID route.
    application.include_router(request_certificates.router)
    application.include_router(requests.router)
    application.include_router(request_delivery.router)
    application.include_router(ai.router)
    application.include_router(consultations.router)
    application.include_router(auth_api.router)
    application.include_router(profile.router)
    application.include_router(admin.router)
    application.include_router(resend_webhook.router)
    if static_dir := os.getenv("KNOWLEDGE_DESK_STATIC_DIR"):
        bundle = Path(static_dir)
        if not (bundle / "index.html").is_file() or not (bundle / "assets").is_dir():
            raise RuntimeError("The configured directory must contain the built frontend.")
        application.mount("/", StaticFiles(directory=bundle, html=True), name="frontend")
    return application


app = create_app()
