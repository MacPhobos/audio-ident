from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.settings import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)
