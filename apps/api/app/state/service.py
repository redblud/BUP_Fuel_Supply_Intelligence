"""Keeps the last trusted NetworkState. Owner: Developer 1.

Foundation reads on demand. TODO(dev1): background poller driven by SSE hints
(simulation.tick -> REST refresh), reset detection, and persisting demand observations.
"""

import logging
from datetime import UTC, datetime

from app.domain.models import NetworkState
from app.simulator.client import SimulatorClient
from app.simulator.errors import SimulatorError
from app.state.reconciler import read_network_state

log = logging.getLogger(__name__)


class StateService:
    def __init__(self, client: SimulatorClient, tick_tolerance: int, fixture: bool) -> None:
        self.client = client
        self._tolerance = tick_tolerance
        self._fixture = fixture
        self.last_trusted: NetworkState | None = None
        self.last_error: SimulatorError | None = None

    async def refresh(self) -> NetworkState | None:
        """Latest state, or None if the simulator could not be read. Never relabels old data as fresh."""
        try:
            state = await read_network_state(self.client, self._tolerance, self._fixture)
        except SimulatorError as exc:
            log.warning("state refresh failed: %s", exc)
            self.last_error = exc
            return None
        self.last_error = None
        if state.meta.freshness in ("FRESH", "FIXTURE"):
            self.last_trusted = state
        return state

    def last_trusted_age_seconds(self) -> float | None:
        if self.last_trusted is None:
            return None
        return round((datetime.now(UTC) - self.last_trusted.meta.retrieved_at).total_seconds(), 3)
