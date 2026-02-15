"""Search orchestrator: parallel lane execution with timeouts and error isolation.

Coordinates the exact (Olaf fingerprint) and vibe (CLAP embedding) search lanes,
running them in parallel when mode=both, with per-lane timeout budgets and
graceful degradation when one lane fails.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from qdrant_client import AsyncQdrantClient

from app.db.session import async_session_factory
from app.schemas.search import ExactMatch, SearchMode, SearchResponse, VibeMatch
from app.search.exact import run_exact_lane
from app.search.vibe import run_vibe_lane

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeout budget
# ---------------------------------------------------------------------------
# Total p95 target: <5s end-to-end.
# Budget: preprocessing (ffmpeg decode) ~1s + max(exact, vibe) <=4s = 5s total.
# Lanes run in parallel, so response time = max(exact_time, vibe_time).

EXACT_TIMEOUT_SECONDS = 3.0
"""Timeout for the exact (Olaf) lane. Typical latency: <500ms."""

VIBE_TIMEOUT_SECONDS = 4.0
"""Timeout for the vibe (CLAP + Qdrant) lane. Typical latency: ~0.5-1.5s."""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SearchUnavailableError(Exception):
    """Both search lanes failed with non-timeout errors."""


class SearchTimeoutError(Exception):
    """Both search lanes timed out."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def orchestrate_search(
    pcm_16k: bytes,
    pcm_48k: bytes,
    mode: SearchMode,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object | None = None,
    clap_processor: object | None = None,
) -> SearchResponse:
    """Orchestrate search across exact and vibe lanes.

    For mode=both, runs lanes in parallel via asyncio.gather with
    return_exceptions=True so one lane failure does not cancel the other.

    Args:
        pcm_16k: 16kHz mono f32le PCM for the exact lane.
        pcm_48k: 48kHz mono f32le PCM for the vibe lane.
        mode: Which lanes to run.
        max_results: Maximum results per lane.
        qdrant_client: Async Qdrant client from app.state.
        clap_model: CLAP model from app.state (required for vibe lane).
        clap_processor: CLAP processor from app.state (required for vibe lane).

    Returns:
        SearchResponse with results from requested lanes.

    Raises:
        SearchUnavailableError: Both lanes failed with non-timeout errors.
        SearchTimeoutError: Both lanes timed out.
    """
    request_id = uuid.uuid4()
    t0 = time.perf_counter()

    exact_matches: list[ExactMatch] = []
    vibe_matches: list[VibeMatch] = []

    if mode == SearchMode.EXACT:
        try:
            exact_matches = await _run_exact_with_timeout(pcm_16k, max_results)
        except TimeoutError:
            raise SearchTimeoutError("Exact search lane timed out") from None
        except Exception as exc:
            raise SearchUnavailableError("Exact search lane failed") from exc
    elif mode == SearchMode.VIBE:
        try:
            vibe_matches = await _run_vibe_with_timeout(
                pcm_48k,
                max_results,
                qdrant_client=qdrant_client,
                clap_model=clap_model,
                clap_processor=clap_processor,
            )
        except TimeoutError:
            raise SearchTimeoutError("Vibe search lane timed out") from None
        except Exception as exc:
            raise SearchUnavailableError("Vibe search lane failed") from exc
    else:
        # BOTH mode: run lanes in parallel
        exact_matches, vibe_matches = await _run_both_lanes(
            pcm_16k=pcm_16k,
            pcm_48k=pcm_48k,
            max_results=max_results,
            qdrant_client=qdrant_client,
            clap_model=clap_model,
            clap_processor=clap_processor,
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return SearchResponse(
        request_id=request_id,
        query_duration_ms=round(elapsed_ms, 2),
        exact_matches=exact_matches,
        vibe_matches=vibe_matches,
        mode_used=mode,
    )


# ---------------------------------------------------------------------------
# Lane runners with timeouts
# ---------------------------------------------------------------------------


async def _run_exact_with_timeout(
    pcm_16k: bytes,
    max_results: int,
) -> list[ExactMatch]:
    """Run the exact lane with a timeout.

    Returns empty list on timeout or error (for single-lane mode, propagates
    the error; for BOTH mode, the caller handles it).
    """
    try:
        return await asyncio.wait_for(
            run_exact_lane(pcm_16k, max_results),
            timeout=EXACT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("Exact lane timed out after %.1fs", EXACT_TIMEOUT_SECONDS)
        raise
    except Exception:
        logger.exception("Exact lane failed")
        raise


async def _run_vibe_with_timeout(
    pcm_48k: bytes,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object | None,
    clap_processor: object | None,
) -> list[VibeMatch]:
    """Run the vibe lane with a timeout, creating a DB session for metadata lookups."""
    async with async_session_factory() as session:
        try:
            return await asyncio.wait_for(
                run_vibe_lane(
                    pcm_48k,
                    max_results,
                    qdrant_client=qdrant_client,
                    clap_model=clap_model,
                    clap_processor=clap_processor,
                    session=session,
                ),
                timeout=VIBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Vibe lane timed out after %.1fs", VIBE_TIMEOUT_SECONDS)
            raise
        except Exception:
            logger.exception("Vibe lane failed")
            raise


async def _run_both_lanes(
    pcm_16k: bytes,
    pcm_48k: bytes,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object | None,
    clap_processor: object | None,
) -> tuple[list[ExactMatch], list[VibeMatch]]:
    """Run both lanes in parallel with independent timeouts.

    Uses asyncio.gather with return_exceptions=True so one lane failure
    does not cancel the other.

    Returns:
        Tuple of (exact_matches, vibe_matches). Either may be empty if
        the corresponding lane failed.

    Raises:
        SearchUnavailableError: Both lanes failed with non-timeout errors.
        SearchTimeoutError: Both lanes timed out.
    """
    exact_task = asyncio.create_task(
        _run_exact_with_timeout(pcm_16k, max_results),
        name="exact_lane",
    )
    vibe_task = asyncio.create_task(
        _run_vibe_with_timeout(
            pcm_48k,
            max_results,
            qdrant_client=qdrant_client,
            clap_model=clap_model,
            clap_processor=clap_processor,
        ),
        name="vibe_lane",
    )

    results = await asyncio.gather(exact_task, vibe_task, return_exceptions=True)

    exact_result = results[0]
    vibe_result = results[1]

    exact_matches: list[ExactMatch] = []
    vibe_matches: list[VibeMatch] = []
    exact_failed = False
    vibe_failed = False
    exact_timeout = False
    vibe_timeout = False

    # Process exact lane result
    if isinstance(exact_result, BaseException):
        exact_failed = True
        exact_timeout = isinstance(exact_result, asyncio.TimeoutError)
        if exact_timeout:
            logger.warning("Exact lane timed out in BOTH mode")
        else:
            logger.error("Exact lane failed in BOTH mode: %s", exact_result)
    else:
        exact_matches = exact_result

    # Process vibe lane result
    if isinstance(vibe_result, BaseException):
        vibe_failed = True
        vibe_timeout = isinstance(vibe_result, asyncio.TimeoutError)
        if vibe_timeout:
            logger.warning("Vibe lane timed out in BOTH mode")
        else:
            logger.error("Vibe lane failed in BOTH mode: %s", vibe_result)
    else:
        vibe_matches = vibe_result

    # Both lanes failed: determine error type
    if exact_failed and vibe_failed:
        if exact_timeout and vibe_timeout:
            raise SearchTimeoutError("Both search lanes timed out")
        raise SearchUnavailableError("Both search lanes failed")

    # Partial failure: return what we have (HTTP 200)
    return exact_matches, vibe_matches
