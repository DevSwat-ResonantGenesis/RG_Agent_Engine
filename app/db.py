import sys
import os
from pathlib import Path

# Add shared modules to path
SHARED_PATH = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_PATH) not in sys.path:
    sys.path.insert(0, str(SHARED_PATH))

# Add service root to path
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

"""Database connection for Agent Engine Service."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from .config import settings

DB_POOL_SIZE = int(os.getenv("AGENT_ENGINE_DB_POOL_SIZE", "8"))
DB_MAX_OVERFLOW = int(os.getenv("AGENT_ENGINE_DB_MAX_OVERFLOW", "7"))
DB_POOL_TIMEOUT = int(os.getenv("AGENT_ENGINE_DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.getenv("AGENT_ENGINE_DB_POOL_RECYCLE", "1800"))
DB_POOL_CLASS = (os.getenv("AGENT_ENGINE_DB_POOL_CLASS", "queue").strip().lower() or "queue")

_engine_kwargs = {
    "echo": False,
}

if DB_POOL_CLASS in ("null", "none"):
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(
        {
            "pool_size": DB_POOL_SIZE,
            "max_overflow": DB_MAX_OVERFLOW,
            "pool_timeout": DB_POOL_TIMEOUT,
            "pool_recycle": DB_POOL_RECYCLE,
            "pool_pre_ping": True,
            "pool_use_lifo": True,
        }
    )

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def get_session():
    async with async_session() as session:
        yield session
