"""StateService pipeline: reconciler labels, reset detection, persistence, allocation lifecycle, degraded state."""

import pytest

from app.persistence import repository
from app.persistence.database import Database
from app.simulator.errors import SimulatorError
from app.state.reconciler import read_network_state
from app.state.service import StateService
from tests.helpers import ScriptedClient


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    await database.init()
    yield database
    await database.close()


def service(client: ScriptedClient, db: Database | None = None, **kwargs) -> StateService:
    return StateService(client, tick_tolerance=1, fixture=False, db=db, **kwargs)


# ---- reconciler labels (#3) ------------------------------------------------------------------------------------------


async def test_clean_read_is_fresh_and_trusted():
    svc = service(ScriptedClient())
    state = await svc.refresh()
    assert state is not None and state.meta.freshness == "FRESH"
    assert (state.meta.tick_start, state.meta.tick_end) == (40, 40) and state.meta.consistent
    assert svc.last_trusted is state


async def test_tick_moving_during_the_read_is_torn_and_not_trusted():
    client = ScriptedClient()
    client.instance_ticks = [40, 43]  # first and second instance read
    svc = service(client)
    state = await svc.refresh()
    assert state.meta.freshness == "TORN" and not state.meta.consistent
    assert (state.meta.tick_start, state.meta.tick_end) == (40, 43)
    assert svc.last_trusted is None and svc.needs_resync


async def test_tick_move_within_tolerance_is_still_consistent():
    client = ScriptedClient()
    client.instance_ticks = [40, 41]
    state = await read_network_state(client, tick_tolerance=1)
    assert state.meta.freshness == "FRESH"


async def test_stale_header_is_labelled_stale_never_fresh():
    client = ScriptedClient()
    client.stale = True
    svc = service(client)
    state = await svc.refresh()
    assert state.meta.freshness == "STALE" and state.meta.stale
    assert svc.last_trusted is None


# ---- degraded state (#7) ---------------------------------------------------------------------------------------------


async def test_unreadable_simulator_keeps_last_trusted_labelled_unavailable():
    client = ScriptedClient()
    svc = service(client)
    good = await svc.refresh()
    client.error = SimulatorError("FAULT_INJECTED", "down", 503)
    assert await svc.refresh() is None
    assert svc.latest is not None
    assert svc.latest.meta.freshness == "UNAVAILABLE"
    assert svc.latest.meta.retrieved_at == good.meta.retrieved_at  # age stays visible
    assert svc.last_trusted is good and svc.needs_resync
    assert svc.last_trusted.meta.freshness == "FRESH"  # the stored trusted copy is untouched


async def test_no_trusted_state_means_no_state():
    client = ScriptedClient()
    client.error = SimulatorError("UNREACHABLE", "down")
    svc = service(client)
    assert await svc.refresh() is None and svc.latest is None


async def test_recovery_after_outage_returns_to_fresh():
    client = ScriptedClient()
    svc = service(client)
    await svc.refresh()
    client.error = SimulatorError("TIMEOUT", "slow")
    await svc.refresh()
    client.error = None
    state = await svc.refresh()
    assert state.meta.freshness == "FRESH" and not svc.needs_resync and svc.last_error is None


async def test_contract_drift_is_an_explicit_error_not_a_crash():
    client = ScriptedClient()
    client.data["stations"][0]["inventory"] = {"DIESEL": "lots"}
    svc = service(client)
    assert await svc.refresh() is None
    assert svc.last_error.code == "CONTRACT_DRIFT"


# ---- reset / run identity (#5) ---------------------------------------------------------------------------------------


async def test_reset_is_detected_and_starts_a_new_run():
    client = ScriptedClient()  # tick 40
    svc = service(client)
    before = await svc.refresh()
    client.reset()  # tick 0, no allocations
    after = await svc.refresh()
    assert before.meta.run_id == "baseline:12345"
    assert after.meta.run_id == "baseline:12345#1"
    assert after.meta.freshness == "RESET_UNCERTAIN"
    assert svc.last_trusted is None  # nothing from the previous run stands in for the new one


async def test_reset_uncertain_clears_after_a_clean_full_resync():
    client = ScriptedClient()
    svc = service(client)
    await svc.refresh()
    client.reset()
    await svc.refresh()
    state = await svc.refresh()  # nothing regressed this time
    assert state.meta.run_id == "baseline:12345#1"
    assert state.meta.freshness == "FRESH" and svc.last_trusted is state


async def test_simulator_notice_marks_the_run_as_new_even_without_regression():
    client = ScriptedClient()
    svc = service(client)
    await svc.refresh()
    svc.note_reset_notice()
    state = await svc.refresh()
    assert state.meta.run_id.endswith("#1") and state.meta.freshness == "RESET_UNCERTAIN"


async def test_normal_progress_is_not_a_reset():
    client = ScriptedClient()
    svc = service(client)
    await svc.refresh()
    client.advance(41)
    state = await svc.refresh()
    assert state.meta.run_id == "baseline:12345" and state.meta.freshness == "FRESH"


async def test_allocation_id_regression_is_a_reset():
    client = ScriptedClient()
    client.add_allocation(9, "k9")
    svc = service(client)
    await svc.refresh()
    client.data["allocations"] = [a for a in client.data["allocations"] if a["id"] < 5]
    client.advance(41)  # tick did not regress, but allocation ids went backwards
    state = await svc.refresh()
    assert state.meta.run_id.endswith("#1")


# ---- persistence, longer history, gaps (#6, #7) ----------------------------------------------------------------------


async def test_planner_history_is_longer_than_the_rest_window(db):
    client = ScriptedClient()
    svc = service(client, db)
    first = await svc.refresh()
    for tick in range(41, 51):  # ten more ticks; REST keeps only the newest 200 rows (~16 ticks)
        client.advance(tick)
        state = await svc.refresh()
    rest_window = len(client.data["demand-history"])
    assert len(state.demand_history) > rest_window
    assert len(state.demand_history) > len(first.demand_history)
    ticks = [o.tick for o in state.demand_history]
    assert ticks == sorted(ticks)
    assert state.history_gaps == []


async def test_missed_ticks_are_marked_as_gaps_not_invented(db):
    client = ScriptedClient()
    svc = service(client, db)
    await svc.refresh()
    client.advance(41)
    await svc.refresh()
    client.advance(60)  # the poller was away for 18 ticks: they were never observed
    state = await svc.refresh()
    gaps = {(g.from_tick, g.to_tick) for g in state.history_gaps}
    assert (42, 59) in gaps
    assert not any(42 <= o.tick <= 59 for o in state.demand_history)


async def test_database_failure_degrades_to_rest_history(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")  # never init(): no tables, so every write fails
    svc = service(ScriptedClient(), database)
    state = await svc.refresh()
    assert state is not None and state.meta.freshness == "FRESH"
    assert svc.persistence_error is not None
    assert len(state.demand_history) == 200  # the REST window, not a fabricated or empty history
    await database.close()


async def test_last_trusted_snapshot_is_persisted(db):
    svc = service(ScriptedClient(), db)
    state = await svc.refresh()
    saved = await repository.load_snapshot(db, state.meta.run_id)
    assert saved is not None and saved.run.tick == 40


# ---- allocation lifecycle (#23) --------------------------------------------------------------------------------------


async def test_allocation_lifecycle_is_tracked_and_queryable(db):
    client = ScriptedClient()
    client.add_allocation(2, "run:rec:0", status="PENDING")
    svc = service(client, db)
    await svc.refresh()
    run_id = svc.latest.meta.run_id

    client.advance(41)
    client.set_allocation_status(2, "IN_TRANSIT", departure_tick=41, expected_arrival_tick=43)
    await svc.refresh()
    client.advance(43)
    client.set_allocation_status(2, "ARRIVED", actual_arrival_tick=43)
    await svc.refresh()
    await svc.refresh()  # replaying a snapshot records nothing new

    tracked = await repository.get_tracked_allocation(db, run_id, 2)
    assert [(t.status, t.tick) for t in tracked.transitions] == [("PENDING", 40), ("IN_TRANSIT", 41), ("ARRIVED", 43)]
    assert tracked.allocation.status == "ARRIVED"

    by_key = await repository.find_tracked_by_idempotency_key(db, run_id, "run:rec:0")
    assert by_key is not None and by_key.allocation.id == 2
    assert await repository.find_tracked_by_idempotency_key(db, run_id, "unknown") is None
    assert await repository.get_tracked_allocation(db, run_id, 999) is None


async def test_allocations_are_scoped_to_their_run(db):
    client = ScriptedClient()
    svc = service(client, db)
    await svc.refresh()
    old_run = svc.latest.meta.run_id
    client.reset()
    await svc.refresh()
    client.add_allocation(1, "fixture-001", status="PENDING")  # the new run reuses allocation id 1
    client.advance(1)
    await svc.refresh()
    new_run = svc.latest.meta.run_id
    assert new_run != old_run
    old = await repository.get_tracked_allocation(db, old_run, 1)
    new = await repository.get_tracked_allocation(db, new_run, 1)
    assert old.allocation.status == "IN_TRANSIT" and new.allocation.status == "PENDING"


async def test_list_tracked_allocations_filters_by_status(db):
    client = ScriptedClient()
    client.add_allocation(2, "k2", status="FAILED", failure_reason="ROUTE_DISRUPTED")
    svc = service(client, db)
    await svc.refresh()
    run_id = svc.latest.meta.run_id
    assert {t.allocation.id for t in await repository.list_tracked_allocations(db, run_id)} == {1, 2}
    failed = await repository.list_tracked_allocations(db, run_id, status="FAILED")
    assert [t.allocation.id for t in failed] == [2]


async def test_allocation_status_lookup_for_the_execution_gateway(db):
    client = ScriptedClient()
    svc = service(client, db)
    assert await svc.allocation_status(allocation_id=1) is None  # nothing observed yet
    await svc.refresh()
    by_id = await svc.allocation_status(allocation_id=1)
    by_key = await svc.allocation_status(idempotency_key="fixture-001")
    assert by_id.allocation.status == "IN_TRANSIT" and by_key.allocation.id == 1
    assert await svc.allocation_status(idempotency_key="never-sent") is None
    with pytest.raises(ValueError):
        await svc.allocation_status()
