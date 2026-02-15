import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.db.engine import engine
from app.routers import health, version
from app.settings import settings

logger = logging.getLogger(__name__)


async def _check_postgres() -> None:
    """Verify PostgreSQL is reachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_qdrant(client: AsyncQdrantClient) -> None:
    """Verify Qdrant is reachable."""
    await client.get_collections()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. Check Postgres
    try:
        await _check_postgres()
        logger.info("PostgreSQL connection verified")
    except Exception as exc:
        raise SystemExit(f"FATAL: Cannot reach PostgreSQL. Error: {exc}") from exc

    # 2. Check Qdrant
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )
    try:
        await _check_qdrant(qdrant)
        logger.info("Qdrant connection verified at %s", settings.qdrant_url)
    except Exception as exc:
        raise SystemExit(
            f"FATAL: Cannot reach Qdrant at {settings.qdrant_url}. Error: {exc}"
        ) from exc

    app.state.qdrant = qdrant

    yield

    # Shutdown
    await qdrant.close()
    await engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(version.router, prefix="/api/v1")

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": None,
                }
            },
        )

    return application


app = create_app()
