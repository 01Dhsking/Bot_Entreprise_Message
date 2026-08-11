import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def database_health() -> dict[str, str | bool]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ok", "connected": True}
    except Exception as exc:
        log.warning("Database health check failed: %s", exc)
        return {"status": "error", "connected": False, "error": str(exc)}


async def wait_for_database(attempts: int = 30, delay_seconds: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        health = await database_health()
        if health["connected"]:
            log.info("PostgreSQL is ready")
            return
        log.info("Waiting for PostgreSQL (%s/%s)", attempt, attempts)
        await asyncio.sleep(delay_seconds)
    raise RuntimeError("PostgreSQL did not become ready in time")


async def close_database() -> None:
    await engine.dispose()
