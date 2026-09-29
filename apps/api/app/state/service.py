"""Keeps the latest and the last trusted NetworkState. Owner: Developer 1.

refresh() is the one full resync: read the simulator, detect a reset, persist observations, merge the stored
demand history, mark gaps, and record allocation transitions. `StateSync` (app/state/sync.py) calls it in the
background; while it runs, `snapshot()` serves the cached result instead of reading the simulator per request.

Trust rules: only FRESH/FIXTURE snapshots become `last_trusted`. When the simulator cannot be read, `latest` is
the last trusted snapshot relabelled UNAVAILABLE (its age stays visible), never FRESH.
"""

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from app.domain.models import NetworkState, TrackedAllocation
from app.persistence import repository
from app.persistence.database import Database
from app.simulator.client import SimulatorClient
from app.simulator.errors import SimulatorError
from app.state.history import find_history_gaps
from app.state.reconciler import read_network_state
from app.state.run_identity import apply_run_identity, detect_reset

log = logging.getLogger(__name__)

SseStatus = Literal["disabled", "connecting", "connected", "reconnecting"]
TRUSTED = ("FRESH", "FIXTURE")


class StateService:
    def __init__(self, client: SimulatorClient, tick_tolerance: int, fixture: bool, db: Database | None = None, history_ticks: int = 96) -> None:
        self.client = client
        self._tolerance = tick_tolerance
        self._fixture = fixture
        self._db = db
        self._history_ticks = history_ticks

        self.latest: NetworkState | None = None
        self.last_trusted: NetworkState | None = None
        self.last_error: SimulatorError | None = None
        self.persistence_error: str | None = None

        self.sync_active = False  # set by StateSync while it keeps `latest` current
        self.needs_resync = False  # last read was untrusted or failed: resync sooner than the slow poll
        self.sse_status: SseStatus = "disabled"
        self.latest_sse_tick: int | None = None

        self._run_sequence = 0
        self._reset_uncertain = False
        self._reset_notice = False
        self._previous_read: NetworkState | None = None
        self._enriched_key: tuple[str, int] | None = None
        self._enriched: tuple[list, list] | None = None
        self._trusted_saved_key: tuple[str, int] | None = None

    # ------------------------------------------------------------------ reads

    async def snapshot(self) -> NetworkState | None:
        """The state to serve: the cached one while background sync runs, else a fresh read."""
        if self.sync_active and (self.latest is not None or self.last_error is not None):
            return self.latest
        await self.refresh()
        return self.latest  # after a failed read: the last trusted state labelled UNAVAILABLE, or None

    async def refresh(self) -> NetworkState | None:
        """Full resync. Returns the new snapshot, or None if the simulator could not be read (see `latest` for the degraded copy)."""
        try:
            state = await read_network_state(self.client, self._tolerance, self._fixture)
        except SimulatorError as exc:
            self._on_failure(exc)
            return None
        except ValidationError as exc:  # the simulator answered, but not in the shape app/domain/models.py expects
            first = exc.errors()[0]["loc"]
            self._on_failure(SimulatorError("CONTRACT_DRIFT", f"{exc.error_count()} field(s) do not match the domain models, first at {first}"))
            return None
        self.last_error = None
        state = self._identify_run(state)
        state = await self._enrich(state)
        self.latest = state
        self.needs_resync = state.meta.freshness not in TRUSTED
        if state.meta.freshness in TRUSTED:
            self.last_trusted = state
            await self._save_trusted(state)
        return state

    async def allocation_status(self, *, allocation_id: int | None = None, idempotency_key: str | None = None) -> TrackedAllocation | None:
        """What is the current status of allocation X / idempotency key K in the current run? None if the simulator never reported it.

        For the execution gateway (Dev 4): after an ambiguous POST, look here before deciding to retry with the same key.
        Reads what the API has observed, so call `refresh()` first when a definitive answer matters.
        """
        if self._db is None or self.latest is None:
            return None
        run_id = self.latest.meta.run_id
        if allocation_id is not None:
            return await repository.get_tracked_allocation(self._db, run_id, allocation_id)
        if idempotency_key is not None:
            return await repository.find_tracked_by_idempotency_key(self._db, run_id, idempotency_key)
        raise ValueError("pass allocation_id or idempotency_key")

    def last_trusted_age_seconds(self) -> float | None:
        if self.last_trusted is None:
            return None
        return round((datetime.now(UTC) - self.last_trusted.meta.retrieved_at).total_seconds(), 3)

    # ------------------------------------------------------------- sync hooks

    def note_sse_tick(self, tick: int) -> None:
        self.latest_sse_tick = tick

    def note_reset_notice(self) -> None:
        """The simulator announced a reset (SSE simulator.notice). The next refresh treats the run as new."""
        self._reset_notice = True

    # --------------------------------------------------------------- pipeline

    def _on_failure(self, exc: SimulatorError) -> None:
        log.warning("state refresh failed: %s", exc)
        self.last_error = exc
        self.needs_resync = True
        trusted = self.last_trusted
        # Keep showing the last trusted state with its real age, labelled so nothing reads it as live.
        self.latest = (
            trusted.model_copy(update={"meta": trusted.meta.model_copy(update={"freshness": "UNAVAILABLE"})}) if trusted is not None else None
        )

    def _identify_run(self, state: NetworkState) -> NetworkState:
        reason = detect_reset(self._previous_read, state)
        if reason is None and self._reset_notice:
            reason = "simulator.notice: Simulation reset"
        self._reset_notice = False
        self._previous_read = state
        if reason is not None:
            self._run_sequence += 1
            self._reset_uncertain = True
            self.last_trusted = None  # data from the previous run must not stand in for this one
            self._enriched_key = None
            log.warning("simulator reset detected (%s); new run sequence %d", reason, self._run_sequence)
        state = apply_run_identity(state, self._run_sequence)
        update: dict = {"latest_sse_tick": self.latest_sse_tick}
        if self._reset_uncertain:
            if reason is not None:
                update["freshness"] = "RESET_UNCERTAIN"  # resync not yet confirmed
            elif state.meta.consistent and not state.meta.stale:
                self._reset_uncertain = False  # a clean full read after the reset: the new run is established
        return state.model_copy(update={"meta": state.meta.model_copy(update=update)})

    async def _enrich(self, state: NetworkState) -> NetworkState:
        """Persist observations, then swap in the longer stored demand history and its gaps. REST data is used if the database fails."""
        if self._db is None:
            return state.model_copy(update={"history_gaps": find_history_gaps(state.demand_history)})
        key = (state.meta.run_id, state.run.tick)
        try:
            if key != self._enriched_key:
                await repository.save_observations(self._db, state)
                history = await repository.load_demand_history(
                    self._db, state.meta.run_id, since_tick=max(0, state.run.tick - self._history_ticks + 1)
                )
                if len(history) >= len(state.demand_history):
                    self._enriched = (history, find_history_gaps(history))
                else:  # stored rows should be a superset of the REST window; if not, do not shorten history
                    self._enriched = (state.demand_history, find_history_gaps(state.demand_history))
                self._enriched_key = key
            await repository.record_allocation_transitions(self._db, state)
            self.persistence_error = None
        except Exception as exc:  # noqa: BLE001 - a failing database must not take the dashboard down
            log.exception("persistence failed")
            self.persistence_error = f"{type(exc).__name__}: {exc}"
            return state.model_copy(update={"history_gaps": find_history_gaps(state.demand_history)})
        history, gaps = self._enriched  # type: ignore[misc]
        return state.model_copy(update={"demand_history": history, "history_gaps": gaps})

    async def _save_trusted(self, state: NetworkState) -> None:
        key = (state.meta.run_id, state.run.tick)
        if self._db is None or key == self._trusted_saved_key:
            return
        try:
            await repository.save_snapshot(self._db, state)
            self._trusted_saved_key = key
        except Exception as exc:  # noqa: BLE001
            log.exception("saving trusted snapshot failed")
            self.persistence_error = f"{type(exc).__name__}: {exc}"
