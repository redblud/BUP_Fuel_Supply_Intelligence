"""FakeSimulatorClient: serves recorded simulator responses from tests/fixtures/<scenario>.json.

Lets intelligence, frontend, and CI work without the real simulator.
A fixture file maps resource names (see client.RESOURCES) to raw /v1/* response bodies,
plus optional "health" and "stale" (true -> every read reports X-Simulator-Stale).
"""

import json
from pathlib import Path
from typing import Any

from app.domain.models import Allocation
from app.simulator.client import SimResponse
from app.simulator.errors import SimulatorError


def load_fixture(fixture_dir: Path, scenario: str) -> dict[str, Any]:
    path = fixture_dir / f"{scenario}.json"
    if not path.exists():
        raise SimulatorError("FIXTURE_NOT_FOUND", str(path))
    return json.loads(path.read_text(encoding="utf-8"))


class FakeSimulatorClient:
    def __init__(self, fixture_dir: Path, scenario: str) -> None:
        self._fixture_dir = fixture_dir
        self.scenario = scenario

    def _data(self) -> dict[str, Any]:
        return load_fixture(self._fixture_dir, self.scenario)

    async def get_health(self) -> dict:
        return self._data()["health"]

    async def get(self, resource: str, **params: Any) -> SimResponse:
        data = self._data()
        if resource not in data:
            raise SimulatorError("NOT_FOUND", f"fixture has no '{resource}'", 404)
        body = data[resource]
        if resource == "demand-history" and "limit" in params:
            body = body[: int(params["limit"])]
        return SimResponse(body, bool(data.get("stale", False)))

    async def create_allocation(self, body: dict) -> Allocation:
        raise SimulatorError("FAKE_READ_ONLY", "Fake simulator does not accept allocations.", 501)

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        raise SimulatorError("FAKE_READ_ONLY", "Fake simulator does not accept allocations.", 501)

    async def admin(self, action: str) -> dict:
        raise SimulatorError("FAKE_READ_ONLY", "Fake simulator has no admin controls.", 501)

    async def close(self) -> None:
        return None
