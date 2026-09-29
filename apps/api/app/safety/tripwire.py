"""Tripwire / Safety Guard. Deterministic, explainable, no LLM. Owner: Developer 4.

Rules cover snapshot trust, run identity, planner failure, forecast drift, persistence, unclear executions, and expired
recommendations, and recommendations whose conditions changed since they were first proposed.
"""

from collections.abc import Sequence

from app.domain.models import NetworkState, Plan, Trip, TripwireStatus

# A forecast whose backtest error exceeds this, over at least DRIFT_MIN_TICKS observed ticks, is drifting.
DRIFT_MAPE = 0.5
DRIFT_MIN_TICKS = 3


def evaluate(state: NetworkState | None, plan: Plan | None, simulator_error: str | None, unknown_executions: int = 0,
             persistence_error: str | None = None, expired_recommendations: Sequence[str] = (),
             review_required: Sequence[str] = ()) -> TripwireStatus:
    """Pure: the current trips for this state, plan, and lifecycle facts. `expired_recommendations` just expired;
    `review_required` changed materially since they were first proposed."""
    trips: list[Trip] = []
    tick = state.run.tick if state else None

    if state is None:
        trips.append(Trip(code="SIMULATOR_UNAVAILABLE", severity="CRITICAL", scope="system", detected_tick=None,
                          message=simulator_error or "No snapshot could be read.",
                          required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
    else:
        if state.meta.freshness == "TORN":
            trips.append(Trip(code="SNAPSHOT_TORN", severity="CRITICAL", scope="system", detected_tick=tick,
                              message=f"Tick moved {state.meta.tick_start}->{state.meta.tick_end} during the read.",
                              required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
        if state.meta.stale:
            trips.append(Trip(code="SNAPSHOT_STALE", severity="CRITICAL", scope="system", detected_tick=tick,
                              message="Simulator reported stale data (X-Simulator-Stale).",
                              required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
        if state.meta.freshness == "UNAVAILABLE":
            trips.append(Trip(code="SIMULATOR_UNAVAILABLE", severity="CRITICAL", scope="system", detected_tick=tick,
                              message=simulator_error or "Simulator unreadable; showing the last trusted snapshot.",
                              required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
        if not state.meta.run_id.strip():
            trips.append(Trip(code="RUN_ID_UNKNOWN", severity="CRITICAL", scope="system", detected_tick=tick,
                              message="The snapshot carries no run id, so orders cannot be tied to a run.",
                              required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
        if state.meta.freshness == "RESET_UNCERTAIN":
            trips.append(Trip(code="RESET_UNCERTAIN", severity="CRITICAL", scope="system", detected_tick=tick,
                              message="Simulator reset detected; waiting for a full resync before trusting this run.",
                              required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))
        for gap_station in sorted({g.station_id for g in state.history_gaps}):
            ticks = [(g.from_tick, g.to_tick) for g in state.history_gaps if g.station_id == gap_station]
            trips.append(Trip(code="HISTORY_GAP", severity="WARNING", scope=gap_station, detected_tick=tick,
                              message=f"Missing demand observations at {gap_station}: ticks {ticks}. Forecast uses only what was observed.",
                              required_actions=["ALERT"]))

    if plan is not None:
        if plan.planner_version == "fallback":
            trips.append(Trip(code="PRIMARY_PLANNER_FAILED", severity="WARNING", scope="system", detected_tick=tick,
                              message="; ".join(plan.warnings), required_actions=["USE_FALLBACK", "REQUIRE_MANUAL_REVIEW"]))
        drifting = sorted({f"{f.station_id}/{f.fuel_type}" for f in plan.forecasts
                           if f.error_mape is not None and f.error_ticks >= DRIFT_MIN_TICKS and f.error_mape > DRIFT_MAPE})
        if drifting:
            trips.append(Trip(code="FORECAST_DRIFT", severity="WARNING", scope="system", detected_tick=tick,
                              message=f"Forecast error above {DRIFT_MAPE:.0%} for {', '.join(drifting)}. Treat recommendations with care.",
                              required_actions=["ALERT"]))
        for risk in plan.risks:
            if "CONNECTIVITY_RISK" in risk.reason_codes:
                trips.append(Trip(code="STATION_UNREACHABLE", severity="WARNING", scope=risk.station_id, detected_tick=tick,
                                  message=f"{risk.station_id} {risk.fuel_type}: {risk.reason}", required_actions=["ALERT"]))

    for rec_id in expired_recommendations:
        trips.append(Trip(code="RECOMMENDATION_EXPIRED", severity="WARNING", scope=rec_id, detected_tick=tick,
                          message=f"{rec_id} expired before it was executed. A new plan replaces it.", required_actions=["REPLAN"]))

    for rec_id in review_required:
        trips.append(Trip(code="RECOMMENDATION_CHANGED", severity="WARNING", scope=rec_id, detected_tick=tick,
                          message=f"{rec_id} changed materially since it was first proposed. Guarded Auto skips it; review and approve it manually.",
                          required_actions=["REQUIRE_MANUAL_REVIEW"]))

    if persistence_error:
        trips.append(Trip(code="PERSISTENCE_FAILURE", severity="CRITICAL", scope="system", detected_tick=tick,
                          message=f"Storage is failing ({persistence_error}). The advisory view still works; execution is frozen.",
                          required_actions=["FREEZE_AUTOMATION", "ALERT"]))

    if unknown_executions:
        trips.append(Trip(code="EXECUTION_UNKNOWN", severity="CRITICAL", scope="system", detected_tick=tick,
                          message=f"{unknown_executions} execution(s) with an unclear outcome. Confirm on the simulator before more orders.",
                          required_actions=["FREEZE_AUTOMATION", "FULL_RESYNC"]))

    return TripwireStatus(state="TRIPPED" if any(t.severity == "CRITICAL" for t in trips) else "CLEAR", trips=trips)
