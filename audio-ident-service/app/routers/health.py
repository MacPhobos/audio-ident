from fastapi import APIRouter

from app.schemas.health import HealthResponse
from app.settings import settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)
