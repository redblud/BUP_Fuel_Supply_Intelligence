"""Decision orchestrator: recommendation lifecycle, pre execution checks, idempotent supervised execution.

The lifecycle transforms (`sync_lifecycle`, `refusal_reason`, `idempotency_key`) are pure. `approve` is the only place
that talks to the simulator, and only through the client the API already owns.
"""

import asyncio
import json
import logging

from app.api.deps import AppContext
from app.domain.models import NetworkState, Plan, Recommendation, RecommendationState, TripwireStatus
from app.intelligence import build_plan
from app.persistence import repository
from app.safety import tripwire
from app.simulator.errors import SimulatorError

log = logging.getLogger(__name__)

UNKNOWN_PREFIX = "EXECUTION_UNKNOWN"


class DecisionError(Exception):
    """An expected refusal, mapped to one HTTP error shape by the route."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def idempotency_key(run_id: str, recommendation_id: str) -> str:
    """Stable per run and recommendation, so a retry after an unclear failure can never create a second shipment."""
    return f"{run_id}/{recommendation_id}".replace(":", "-").replace("#", "-")


def is_unknown(state: RecommendationState) -> bool:
    """True when the POST outcome is unclear and must be confirmed before anything else executes."""
    return state.status == "EXECUTING" and (state.message or "").startswith(UNKNOWN_PREFIX)


def sync_lifecycle(states: dict[str, RecommendationState], plan: Plan) -> dict[str, RecommendationState]:
    """Register new recommendations, expire old ones, and supersede ones the planner no longer proposes. Returns a new dict."""
    now = plan.tick
    current = {r.id: r for r in plan.recommendations}
    out = dict(states)
    for rec_id in current:
        if rec_id not in out:
            out[rec_id] = RecommendationState(recommendation_id=rec_id, status="PROPOSED", updated_tick=now)
    for rec_id, st in states.items():
        if st.status != "PROPOSED":
            continue
        rec = current.get(rec_id)
        if rec is None:
            out[rec_id] = st.model_copy(update={"status": "SUPERSEDED", "updated_tick": now, "message": "The planner no longer proposes this."})
        elif now > rec.expiry_tick:
            out[rec_id] = st.model_copy(update={"status": "EXPIRED", "updated_tick": now, "message": f"Expired at tick {rec.expiry_tick}."})
    return out


QUANTITY_CHANGE = 0.10  # a replacement shipment differing by more than this from what was first proposed needs a human look


def material_change(prev: Recommendation, current: Recommendation) -> bool:
    """True when the replacement changes where the fuel comes from, which way it travels, or how much moves."""
    if (prev.request.source_depot_id, prev.request.route_id) != (current.request.source_depot_id, current.request.route_id):
        return True
    return abs(current.request.quantity - prev.request.quantity) > QUANTITY_CHANGE * max(prev.request.quantity, 1.0)


def _slots(plan: Plan) -> dict[str, tuple[str, str, int]]:
    """Stable key per shipment: (station, fuel, nth shipment for that pair). Ids embed the tick, so they cannot be the key."""
    seen: dict[tuple[str, str], int] = {}
    out: dict[str, tuple[str, str, int]] = {}
    for rec in plan.recommendations:
        pair = (rec.station_id, rec.fuel_type)
        seen[pair] = seen.get(pair, 0) + 1
        out[rec.id] = (*pair, seen[pair])
    return out


def review_flags(
    baselines: dict[tuple[str, str, int], Recommendation],
    states: dict[str, RecommendationState],
    flagged: frozenset[str],
    plan: Plan,
) -> tuple[dict[tuple[str, str, int], Recommendation], frozenset[str]]:
    """Pure. Replans issue new ids each tick, so compare each recommendation with the still-PROPOSED one it replaced.

    A material difference (or an earlier flag on the replaced one) marks the new recommendation as needing manual review.
    Returns the updated baselines and the ids to review.
    """
    new_baselines = dict(baselines)
    review: set[str] = set()
    for rec_id, slot in _slots(plan).items():
        rec = next(r for r in plan.recommendations if r.id == rec_id)
        prev = baselines.get(slot)
        new_baselines[slot] = rec
        if prev is None:
            continue
        if prev.id == rec.id:
            if rec.id in flagged:
                review.add(rec.id)
            continue
        prev_state = states.get(prev.id)
        if prev_state is not None and prev_state.status == "PROPOSED" and (prev.id in flagged or material_change(prev, rec)):
            review.add(rec.id)
    return new_baselines, frozenset(review)


def sync_plan(ctx: AppContext, plan: Plan) -> None:
    """Fold a fresh plan into the context: review flags first (they need the old states), then the lifecycle."""
    ctx.baselines, ctx.review_required = review_flags(ctx.baselines, ctx.recommendation_states, ctx.review_required, plan)
    ctx.recommendation_states = sync_lifecycle(ctx.recommendation_states, plan)


async def track_outcomes(ctx: AppContext) -> None:
    """Attach the observed allocation lifecycle (PENDING, IN_TRANSIT, ARRIVED, ...) to DONE recommendations."""
    updated = dict(ctx.recommendation_states)
    for rec_id, st in ctx.recommendation_states.items():
        if st.status != "DONE" or st.allocation_id is None:
            continue
        tracked = await ctx.state.allocation_status(allocation_id=st.allocation_id)
        if tracked is not None and tracked.allocation.status != st.allocation_status:
            updated[rec_id] = st.model_copy(update={"allocation_status": tracked.allocation.status})
    ctx.recommendation_states = updated


def just_expired(states: dict[str, RecommendationState], tick: int) -> list[str]:
    """Ids that expired on this tick, so the Tripwire reports each expiry once instead of forever."""
    return sorted(rid for rid, st in states.items() if st.status == "EXPIRED" and st.updated_tick == tick)


def refusal_reason(rec: Recommendation | None, trip: TripwireStatus, kill_switch: bool, tick: int) -> DecisionError | None:
    """Why this recommendation may not execute right now, or None."""
    if kill_switch:
        return DecisionError("KILL_SWITCH", "The kill switch is on: nothing executes.")
    if trip.state == "TRIPPED":
        codes = ", ".join(t.code for t in trip.trips if t.severity == "CRITICAL")
        return DecisionError("TRIPWIRE_TRIPPED", f"Safety guard is tripped ({codes}). Resolve it before executing.")
    if rec is None:
        return DecisionError("STALE_RECOMMENDATION", "The planner no longer proposes this on the current state. Review the new plan.")
    if tick > rec.expiry_tick:
        return DecisionError("RECOMMENDATION_EXPIRED", f"Expired at tick {rec.expiry_tick}; now tick {tick}.")
    return None


async def _persist(
    ctx: AppContext, st: RecommendationState, note: str, *, run_id: str | None = None, key: str | None = None, body: str | None = None
) -> None:
    """Keep the lifecycle in memory, store it durably, and audit it. A failing database is surfaced as PERSISTENCE_FAILURE, never a crash."""
    ctx.recommendation_states = {**ctx.recommendation_states, st.recommendation_id: st}
    log.info(note, extra={"component": "decision", "decision_id": st.recommendation_id, "allocation_id": st.allocation_id})
    try:
        await ctx.db.record("decision", json.dumps({"note": note, **st.model_dump()}))
        if run_id is not None:
            await repository.save_intent(ctx.db, st, run_id=run_id, idempotency_key=key, request_body=body)
    except Exception as exc:  # noqa: BLE001
        ctx.state.persistence_error = f"{type(exc).__name__}: {exc}"
        log.error("audit write failed", extra={"component": "persistence", "error_code": "PERSISTENCE_FAILURE"})


def _trip(ctx: AppContext, state: NetworkState, plan: Plan, ignore_unknown_for: str | None = None) -> TripwireStatus:
    unknown = sum(1 for s in ctx.recommendation_states.values() if is_unknown(s) and s.recommendation_id != ignore_unknown_for)
    return tripwire.evaluate(state, plan, str(ctx.state.last_error) if ctx.state.last_error else None, unknown_executions=unknown,
                             persistence_error=ctx.state.persistence_error,
                             expired_recommendations=just_expired(ctx.recommendation_states, state.run.tick),
                             review_required=sorted(ctx.review_required))


async def approve(ctx: AppContext, recommendation_id: str) -> RecommendationState:
    """Revalidate against a fresh snapshot, check the guards, then execute once. Idempotent per recommendation."""
    existing = ctx.recommendation_states.get(recommendation_id)
    retrying = existing is not None and is_unknown(existing)
    if existing is not None and not retrying:
        if existing.status in ("DONE", "EXECUTING"):
            return existing
        if existing.status != "PROPOSED":
            raise DecisionError("NOT_EXECUTABLE", f"Recommendation is {existing.status}.")

    state = await ctx.state.refresh()
    if state is None:
        raise DecisionError("SNAPSHOT_UNAVAILABLE", "No fresh trusted snapshot; not executing.", 503)
    plan = build_plan(state)
    sync_plan(ctx, plan)
    rec = next((r for r in plan.recommendations if r.id == recommendation_id), None)
    if rec is None and existing is None:
        raise DecisionError("NOT_FOUND", "Unknown recommendation.", 404)
    refusal = refusal_reason(rec, _trip(ctx, state, plan, ignore_unknown_for=recommendation_id), ctx.automation.kill_switch, state.run.tick)
    if refusal is not None:
        raise refusal
    assert rec is not None

    key = idempotency_key(state.meta.run_id, rec.id)
    lock = ctx.depot_locks.setdefault(rec.request.source_depot_id, asyncio.Lock())
    if lock.locked():
        raise DecisionError("DEPOT_BUSY", f"Another execution from {rec.request.source_depot_id} is in progress.")
    async with lock:
        seen = await ctx.state.allocation_status(idempotency_key=key)
        if seen is not None:  # the simulator already has it: adopt it, never POST again
            done = RecommendationState(recommendation_id=rec.id, status="DONE", updated_tick=state.run.tick, allocation_id=seen.allocation.id,
                                       message="Already created on the simulator.")
            await _persist(ctx, done, "adopted", run_id=state.meta.run_id)
            return done
        stored = await repository.load_intent_body(ctx.db, rec.id) if retrying else None
        if stored is not None:  # replay the exact body and key that were prepared, never a rebuilt one
            key, raw = stored
            body = json.loads(raw)
        else:
            body = {**rec.request.model_dump(), "idempotency_key": key}
            raw = json.dumps(body, sort_keys=True)
        prepared = RecommendationState(recommendation_id=rec.id, status="EXECUTING", updated_tick=state.run.tick, message="PREPARED")
        await _persist(ctx, prepared, "prepared", run_id=state.meta.run_id, key=key, body=raw)
        if ctx.state.persistence_error:  # the intent must be durable before anything is sent
            raise DecisionError("PERSISTENCE_FAILURE", "Could not store the execution intent; not sending.", 503)
        error: SimulatorError | None = None
        for _ in range(2):  # one retry, same key and body
            ctx.counters["allocation_attempts"] += 1
            try:
                alloc = await ctx.state.client.create_allocation(body)
            except SimulatorError as exc:
                error = exc
                ctx.counters["allocation_conflicts" if exc.status == 409 else "allocation_failures"] += 1
                log.warning("allocation rejected", extra={"component": "decision", "decision_id": rec.id, "error_code": exc.code})
                if not exc.retryable:
                    break
                continue
            done = RecommendationState(recommendation_id=rec.id, status="DONE", updated_tick=state.run.tick, allocation_id=alloc.id,
                                       message=f"Allocation {alloc.id} {alloc.status}.")
            await _persist(ctx, done, "executed", run_id=state.meta.run_id)
            return done
        assert error is not None
        if error.retryable:
            message = f"{UNKNOWN_PREFIX}: {error}. Confirm on the simulator, then approve again (same key)."
            await _persist(ctx, RecommendationState(recommendation_id=rec.id, status="EXECUTING", updated_tick=state.run.tick, message=message),
                           "unknown", run_id=state.meta.run_id)
            raise DecisionError("EXECUTION_UNKNOWN", message, 502)
        await _persist(ctx, RecommendationState(recommendation_id=rec.id, status="FAILED", updated_tick=state.run.tick, message=str(error)), "failed",
                       run_id=state.meta.run_id)
        raise DecisionError(error.code, error.message, 502)


async def reject(ctx: AppContext, recommendation_id: str) -> RecommendationState:
    """Operator declines a proposed recommendation."""
    st = ctx.recommendation_states.get(recommendation_id)
    if st is None:
        raise DecisionError("NOT_FOUND", "Unknown recommendation.", 404)
    if st.status == "REJECTED":
        return st
    if st.status != "PROPOSED":
        raise DecisionError("NOT_REJECTABLE", f"Recommendation is {st.status}.")
    out = st.model_copy(update={"status": "REJECTED", "message": "Rejected by the operator."})
    latest = ctx.state.latest
    await _persist(ctx, out, "rejected", run_id=latest.meta.run_id if latest else None)
    return out
