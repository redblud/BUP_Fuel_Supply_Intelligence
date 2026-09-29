"""Allocation lifecycle observation. Pure: turns a snapshot into the transitions not yet recorded.

We observe, we do not submit: submission belongs to the execution gateway. A status can be skipped
(we may first see an allocation already IN_TRANSIT); only statuses actually observed are recorded.
"""

from datetime import datetime

from app.domain.models import Allocation, AllocationTransition, NetworkState


def transition_tick(allocation: Allocation, observed_tick: int) -> int:
    """The simulator tick this status applies to: the simulator's own timestamp when it gives one, else when we saw it."""
    if allocation.status == "PENDING":
        return allocation.created_tick
    if allocation.status == "IN_TRANSIT" and allocation.departure_tick is not None:
        return allocation.departure_tick
    if allocation.status == "ARRIVED" and allocation.actual_arrival_tick is not None:
        return allocation.actual_arrival_tick
    return observed_tick


def new_transitions(state: NetworkState, known: set[tuple[int, str]], observed_at: datetime) -> list[AllocationTransition]:
    """Transitions in `state` whose (allocation_id, status) is not in `known`, in allocation order."""
    return [
        AllocationTransition(
            run_id=state.meta.run_id,
            allocation_id=a.id,
            status=a.status,
            tick=transition_tick(a, state.run.tick),
            observed_at=observed_at,
        )
        for a in sorted(state.allocations, key=lambda a: a.id)
        if (a.id, a.status) not in known
    ]
