"""Demand-history gap detection. Pure. Gaps are reported, never filled with invented observations."""

from collections import defaultdict

from app.domain.models import DemandObservation, HistoryGap


def find_history_gaps(history: list[DemandObservation]) -> list[HistoryGap]:
    """Missing tick ranges per (station, fuel), between the first and last observed tick of that series."""
    ticks: dict[tuple[str, str], set[int]] = defaultdict(set)
    for obs in history:
        ticks[(obs.station_id, obs.fuel_type)].add(obs.tick)
    gaps: list[HistoryGap] = []
    for (station_id, fuel_type), seen in sorted(ticks.items()):
        ordered = sorted(seen)
        for before, after in zip(ordered, ordered[1:], strict=False):
            if after - before > 1:
                gaps.append(HistoryGap(station_id=station_id, fuel_type=fuel_type, from_tick=before + 1, to_tick=after - 1))  # type: ignore[arg-type]
    return gaps
