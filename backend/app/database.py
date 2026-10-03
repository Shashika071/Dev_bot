"""
Async SQLAlchemy database setup for PostgreSQL.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

# Docker/internal Postgres has no TLS. Disabling SSL avoids asyncpg
# trying a TLS path against a hostname that may not resolve as expected.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={"ssl": False},
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency for database sessions."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """
    Bootstrap schema then apply Alembic migrations.

    create_all is safe for brand-new DBs; Alembic adds/upgrades columns
    on existing VPS pgdata without destructive drops.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _run_alembic_upgrade()


async def _run_alembic_upgrade() -> None:
    """Best-effort alembic upgrade head (sync URL from async settings)."""
    try:
        from alembic import command
        from alembic.config import Config
        import os

        root = os.path.dirname(os.path.dirname(__file__))
        cfg = Config(os.path.join(root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(root, "alembic"))
        url = settings.database_url
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
    except Exception as e:
        # Never block API startup if migration tooling fails; create_all already ran.
        import structlog

        structlog.get_logger(__name__).warning("alembic_upgrade_skipped", error=str(e))
