"""Alembic-backed persistence: schema, observations, last trusted snapshot, survival across a restart."""

import pytest
import sqlalchemy as sa

from app.persistence import repository
from app.persistence.database import Database
from tests.conftest import load_state


@pytest.fixture
def state():
    return load_state("route-disruption")  # sync fixture: load_state runs its own event loop


async def test_migrations_create_tables_and_record_startup(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    await db.init()
    async with db.engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
    assert {"simulation_runs", "demand_observations", "snapshots", "station_state_observations", "system_events", "alembic_version"} <= tables
    assert await db.ping() == 1
    await db.close()


async def test_init_upgrades_a_foundation_database(tmp_path):
    """A DB made by the foundation create_all (system_events only, no alembic_version) must still upgrade."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"
    engine = sa.create_engine(url.replace("+aiosqlite", ""))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE system_events "
                "(id INTEGER PRIMARY KEY, kind VARCHAR(64) NOT NULL, detail VARCHAR(500) NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(sa.text("INSERT INTO system_events (kind, detail, created_at) VALUES ('startup', '', '2026-01-01')"))
    engine.dispose()
    db = Database(url)
    await db.init()
    assert await db.ping() == 2
    await db.close()


async def test_observations_are_idempotent_and_ordered(tmp_path, state):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    await db.init()
    await repository.save_observations(db, state)
    await repository.save_observations(db, state)  # same tick again: no duplicates
    history = await repository.load_demand_history(db, state.meta.run_id)
    assert len(history) == len(state.demand_history)
    assert [o.tick for o in history] == sorted(o.tick for o in history)
    newest = await repository.load_demand_history(db, state.meta.run_id, limit=12)
    assert {o.tick for o in newest} == {max(o.tick for o in state.demand_history)}
    one = await repository.load_demand_history(db, state.meta.run_id, station_id="station-tongi", fuel_type="DIESEL")
    assert one and all(o.station_id == "station-tongi" and o.fuel_type == "DIESEL" for o in one)
    await db.close()


async def test_data_survives_restart(tmp_path, state):
    url = f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"
    db = Database(url)
    await db.init()
    await repository.save_observations(db, state)
    await repository.save_snapshot(db, state)
    await db.close()

    db = Database(url)  # the compose "restart backend" case
    await db.init()
    restored = await repository.load_snapshot(db, state.meta.run_id)
    assert restored is not None and restored.run.tick == state.run.tick
    assert len(await repository.load_demand_history(db, state.meta.run_id)) == len(state.demand_history)
    assert await db.ping() == 2
    await db.close()
