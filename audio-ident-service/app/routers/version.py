import os
import subprocess  # nosec B404
from functools import lru_cache

from fastapi import APIRouter

from app.schemas.version import VersionResponse
from app.settings import settings

router = APIRouter(tags=["version"])


@lru_cache(maxsize=1)
def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(  # nosec
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


@router.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    return VersionResponse(
        name=settings.app_name,
        version=settings.app_version,
        git_sha=_git_sha(),
        build_time=os.environ.get("BUILD_TIME", "unknown"),
    )
