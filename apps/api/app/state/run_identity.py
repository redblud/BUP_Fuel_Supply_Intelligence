"""Run identity and reset detection. Pure: compares two snapshots, no I/O.

The simulator keeps scenario and seed across `/admin/reset`, so `scenario:seed` alone cannot tell two runs
apart. A run is `scenario:seed#n`, where n increments each time a reset is detected (n = 0 has no suffix).
Identity everywhere is (run_id, allocation_id).
"""

from app.domain.models import NetworkState


def detect_reset(previous: NetworkState | None, current: NetworkState) -> str | None:
    """Why `current` cannot be a continuation of `previous`, or None if it can.

    Signals: scenario/seed change, tick or sim_time regression, allocation ids that went backwards
    or were reused for a different idempotency key.
    """
    if previous is None:
        return None
    if (previous.run.scenario_id, previous.run.seed) != (current.run.scenario_id, current.run.seed):
        return "scenario or seed changed"
    if current.run.tick < previous.run.tick:
        return f"tick regressed {previous.run.tick} -> {current.run.tick}"
    if current.run.sim_time < previous.run.sim_time:
        return "sim_time regressed"
    previous_ids = {a.id: a.idempotency_key for a in previous.allocations}
    current_ids = {a.id: a.idempotency_key for a in current.allocations}
    if previous_ids and (max(current_ids, default=0) < max(previous_ids)):
        return "allocation ids regressed"
    if any(key != current_ids[i] for i, key in previous_ids.items() if i in current_ids):
        return "allocation id reused for a different idempotency key"
    return None


def run_id_with_sequence(base_run_id: str, sequence: int) -> str:
    """`scenario:seed` for the first run, `scenario:seed#n` after n detected resets."""
    return base_run_id if sequence == 0 else f"{base_run_id}#{sequence}"


def apply_run_identity(state: NetworkState, sequence: int) -> NetworkState:
    """Copy of `state` whose meta carries the run id for this reset sequence."""
    base = state.meta.run_id.split("#", 1)[0]
    return state.model_copy(update={"meta": state.meta.model_copy(update={"run_id": run_id_with_sequence(base, sequence)})})
