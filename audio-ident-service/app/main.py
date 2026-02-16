import logging
import shutil
import time as _time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.auth.admin import AdminAuthError
from app.db.engine import engine
from app.routers import health, ingest, search, tracks, version
from app.settings import settings

logger = logging.getLogger(__name__)


async def _check_postgres() -> None:
    """Verify PostgreSQL is reachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_qdrant(client: AsyncQdrantClient) -> None:
    """Verify Qdrant is reachable."""
    await client.get_collections()


def _get_torch_device() -> str:
    """Detect best available compute device for model inference."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            # CLAP uses ops not yet supported on MPS (Placeholder storage error).
            # Fall through to CPU until PyTorch/MPS support improves.
            logger.info("MPS available but not used for CLAP (unsupported ops)")
    except ImportError:
        logger.warning("torch not installed, defaulting to CPU")
    return "cpu"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 0. Check ffmpeg availability
    if not shutil.which("ffmpeg"):
        raise SystemExit(
            "FATAL: ffmpeg not found on PATH. "
            "Install via: brew install ffmpeg (macOS) or apt install ffmpeg (Ubuntu)."
        )
    logger.info("ffmpeg found on PATH")

    # 1. Check Postgres
    try:
        await _check_postgres()
        logger.info("PostgreSQL connection verified")
    except Exception as exc:
        logger.debug("PostgreSQL connection error: %s", exc)
        raise SystemExit(
            "FATAL: Cannot reach PostgreSQL. "
            "Check DATABASE_URL and ensure the server is running."
        ) from exc

    # 2. Check Qdrant
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )
    try:
        await _check_qdrant(qdrant)
        logger.info("Qdrant connection verified at %s", settings.qdrant_url)
    except Exception as exc:
        logger.debug("Qdrant connection error: %s", exc)
        raise SystemExit(
            "FATAL: Cannot reach Qdrant. " "Check QDRANT_URL and ensure the server is running."
        ) from exc

    app.state.qdrant = qdrant

    # 3. Pre-load CLAP model (HuggingFace Transformers)
    device = _get_torch_device()
    logger.info("CLAP inference device: %s", device)

    t_model = _time.perf_counter()
    logger.info("Loading CLAP embedding model (HF Transformers)...")

    try:
        from transformers import ClapModel, ClapProcessor

        processor = ClapProcessor.from_pretrained("laion/larger_clap_music_and_speech")
        model = ClapModel.from_pretrained("laion/larger_clap_music_and_speech")
        model.eval()

        if device != "cpu":
            model = model.to(device)

        app.state.clap_model = model
        app.state.clap_processor = processor

        load_time = _time.perf_counter() - t_model
        logger.info("CLAP model loaded in %.1fs (device: %s)", load_time, device)
        if load_time > 5:
            logger.warning(
                "CLAP model load took %.1fs (expected ~1s with HF Transformers) -- "
                "investigate network or disk issues",
                load_time,
            )

        # 4. Warm-up inference (prevent cold-start latency on first request)
        import numpy as np

        warmup_audio = np.zeros(48000 * 5, dtype=np.float32)  # 5s silence
        inputs = processor(audio=[warmup_audio], sampling_rate=48000, return_tensors="pt")
        if device != "cpu":
            inputs = {k: v.to(device) for k, v in inputs.items()}
        _ = model.get_audio_features(**inputs)
        logger.info("CLAP warm-up inference complete")

    except Exception as exc:
        logger.warning("CLAP model loading failed: %s. Vibe search will be unavailable.", exc)
        app.state.clap_model = None
        app.state.clap_processor = None

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
    application.include_router(search.router, prefix="/api/v1")
    application.include_router(tracks.router, prefix="/api/v1")
    application.include_router(ingest.router, prefix="/api/v1")

    @application.exception_handler(AdminAuthError)
    async def admin_auth_error_handler(request: Request, exc: AdminAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": None,
                }
            },
        )

    return application


app = create_app()
