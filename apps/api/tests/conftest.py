import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT, Settings
from app.domain.models import NetworkState
from app.main import create_app
from app.simulator.fake import FakeSimulatorClient
from app.state.reconciler import read_network_state

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def load_state(scenario: str) -> NetworkState:
    """NetworkState from a fixture scenario, through the real mapper/reconciler path."""
    return asyncio.run(read_network_state(FakeSimulatorClient(FIXTURES, scenario), tick_tolerance=1, fixture=True))


@pytest.fixture
def client(tmp_path: Path):
    def make(scenario: str = "route-disruption") -> TestClient:
        settings = Settings(simulator_mode="fake", fixture_scenario=scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        return TestClient(create_app(settings))

    return make
