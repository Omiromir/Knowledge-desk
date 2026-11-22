import asyncio

from anyio import to_thread
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import database, postgres, rag, uploads, vector_index

router = APIRouter(tags=["health"])


def database_ready() -> bool:
    with database.connect() as connection:
        if postgres.database_url():
            database.check_postgres_schema(connection)
        else:
            connection.execute("SELECT id FROM documents LIMIT 0")
    return True


def index_ready() -> bool:
    if postgres.database_url():
        with database.connect() as connection:
            connection.execute("SELECT embedding FROM rag_chunks WHERE profile = ? LIMIT 1", (vector_index.profile(),))
        return True
    manifest = rag.manifest_collection()
    _, versions, _ = uploads.active_corpus()
    if versions:
        uploads.collection().count()
    if manifest is not None:
        manifest.count()
    return bool(versions) or manifest is not None


async def probe(check) -> bool:
    try:
        async with asyncio.timeout(3):
            return await to_thread.run_sync(check, abandon_on_cancel=True)
    except Exception:
        return False


@router.get("/health")
async def health():
    db, index = await asyncio.gather(probe(database_ready), probe(index_ready))
    ready = db and index
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "database": "ok" if db else "unavailable",
         "index": "ok" if index else "unavailable"},
        status_code=200 if ready else 503, headers={"Cache-Control": "no-store"},
    )
