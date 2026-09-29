"""SQLite (WAL) via async SQLAlchemy. Owner: Developer 1 (observations) / Developer 4 (decisions, audit).

Foundation only proves backend -> SQLAlchemy -> SQLite -> persistent volume.
Add real tables through Alembic migrations as features land.
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, String, event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Database:
    def __init__(self, url: str) -> None:
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
        # TODO(dev4): replace create_all with Alembic migrations once real tables exist.
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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
