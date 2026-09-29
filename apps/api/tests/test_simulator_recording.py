"""Recorder and contract-drift checks. Recorded fixtures (tests/fixtures/recorded-*.json) must map onto NetworkState."""

import copy
import json

import httpx
import pytest

from app.simulator.client import RealSimulatorClient
from app.simulator.contract import find_drift, map_recording
from app.simulator.recorder import record, write_recording
from tests.conftest import FIXTURES

RECORDED = sorted(FIXTURES.glob("recorded-*.json"))
BASE = json.loads((FIXTURES / "route-disruption.json").read_text(encoding="utf-8"))
PATHS = {f"/v1/{name}": name for name in BASE if name != "health"}


def _mock_simulator(stale: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health":
            return httpx.Response(200, json=BASE["health"])
        headers = {"X-Simulator-Stale": "true"} if stale else {}
        return httpx.Response(200, json=BASE[PATHS[request.url.path]], headers=headers)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("path", RECORDED, ids=lambda p: p.stem)
def test_recorded_responses_match_domain_models(path):
    state = map_recording(json.loads(path.read_text(encoding="utf-8")))
    assert state.run.tick >= 0


def test_hand_made_fixture_has_no_drift():
    assert find_drift(BASE) == []


def test_drift_names_the_exact_field():
    raw = copy.deepcopy(BASE)
    raw["stations"][1]["inventory"] = {"DIESEL": "lots"}
    drift = find_drift(raw)
    assert drift
    assert any(line.startswith("stations[1].inventory.DIESEL") for line in drift)


def test_missing_resource_is_reported():
    raw = {k: v for k, v in BASE.items() if k != "routes"}
    assert find_drift(raw) == ["routes: resource missing from recording"]


async def test_record_captures_every_resource_and_round_trips(tmp_path):
    client = RealSimulatorClient("http://sim", 1.0, 1.0, transport=_mock_simulator())
    recording = await record(client)
    await client.close()
    assert recording["stale"] is False
    assert {k: v for k, v in recording.items() if k != "stale"} == {k: v for k, v in BASE.items() if k != "stale"}

    out = tmp_path / "recorded-x.json"
    write_recording(recording, out)
    assert find_drift(json.loads(out.read_text(encoding="utf-8"))) == []


async def test_record_flags_stale_reads():
    client = RealSimulatorClient("http://sim", 1.0, 1.0, transport=_mock_simulator(stale=True))
    assert (await record(client))["stale"] is True
    await client.close()
