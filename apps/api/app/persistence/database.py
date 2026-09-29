"""SQLite (WAL) via async SQLAlchemy. Owner: Developer 1 (observations) / Developer 4 (decisions, audit).

The schema is owned by Alembic (`alembic/versions`); `Database.init` upgrades to head at startup.
Table definitions live in `app/persistence/models.py`, queries in `app/persistence/repository.py`.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.persistence.models import SystemEvent

# apps/api locally, /app in Docker: both hold alembic.ini next to the `app` package.
API_ROOT = Path(__file__).resolve().parents[2]


def migrate(url: str) -> None:
    """Upgrade the database at `url` to the latest revision. Blocking; call via a thread from async code."""
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url
    command.upgrade(cfg, "head")


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("sqlite") and ":///" in url:
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    async def init(self) -> None:
        await asyncio.to_thread(migrate, self.url)
        await self.record("startup")

    async def record(self, kind: str, detail: str = "") -> None:
        async with self.sessions() as s:
            s.add(SystemEvent(kind=kind, detail=detail))
            await s.commit()

    async def ping(self) -> int:
        """Returns the number of recorded startups; proves the volume persists across restarts."""
        async with self.sessions() as s:
            await s.execute(text("SELECT 1"))
            return await s.scalar(select(func.count()).select_from(SystemEvent).where(SystemEvent.kind == "startup")) or 0

    async def close(self) -> None:
        await self.engine.dispose()
