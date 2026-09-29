"""Tripwire / Safety Guard. Deterministic, explainable, no LLM. Owner: Developer 4.

Foundation rules cover snapshot trust and planner failure.
TODO(dev4): RESET_UNCERTAIN, EXECUTION_UNKNOWN, PERSISTENCE_FAILURE, RECOMMENDATION_EXPIRED,
FORECAST_DRIFT, per-recommendation checks.
"""

from app.domain.models import NetworkState, Plan, Trip, TripwireStatus


def evaluate(state: NetworkState | None, plan: Plan | None, simulator_error: str | None) -> TripwireStatus:
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
        for risk in plan.risks:
            if "CONNECTIVITY_RISK" in risk.reason_codes:
                trips.append(Trip(code="STATION_UNREACHABLE", severity="WARNING", scope=risk.station_id, detected_tick=tick,
                                  message=f"{risk.station_id} {risk.fuel_type}: {risk.reason}", required_actions=["ALERT"]))

    return TripwireStatus(state="TRIPPED" if any(t.severity == "CRITICAL" for t in trips) else "CLEAR", trips=trips)
