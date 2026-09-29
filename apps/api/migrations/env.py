"""Alembic environment: async SQLite. The URL comes from cfg.attributes["sqlalchemy_url"] (set by Database.init) or alembic.ini."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("sqlalchemy_url") or config.get_main_option("sqlalchemy.url")


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_migrate)
    await engine.dispose()


asyncio.run(_run())
