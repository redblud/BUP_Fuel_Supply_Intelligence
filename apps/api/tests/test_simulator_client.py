"""RealSimulatorClient behavior against httpx.MockTransport: retries, error shapes, typed allocations."""

from collections.abc import Callable

import httpx
import pytest

from app.simulator.client import RealSimulatorClient
from app.simulator.errors import SimulatorError

ALLOCATION = {
    "id": 7,
    "idempotency_key": "run:rec:0",
    "source_depot_id": "depot-gazipur",
    "destination_station_id": "station-tongi",
    "route_id": "route-gazipur-tongi",
    "fuel_type": "DIESEL",
    "quantity": 5000,
    "created_tick": 3,
    "departure_tick": None,
    "expected_arrival_tick": None,
    "actual_arrival_tick": None,
    "status": "PENDING",
    "failure_reason": None,
}
FAULT = {"error": {"code": "FAULT_INJECTED", "message": "injected"}}


class Harness:
    """Client wired to a scripted transport; records requests and backoff sleeps."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response], max_retries: int = 2) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.client = RealSimulatorClient(
            "http://sim", 1.0, 1.0, transport=httpx.MockTransport(record), max_retries=max_retries, sleep=sleep, jitter=lambda: 1.0
        )


def sequence(*responses: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    remaining = list(responses)

    def handler(_: httpx.Request) -> httpx.Response:
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


async def test_get_retries_fault_injected_then_succeeds():
    h = Harness(sequence(httpx.Response(503, json=FAULT), httpx.Response(503, json=FAULT), httpx.Response(200, json=[])))
    response = await h.client.get("regions")
    assert response.body == []
    assert len(h.requests) == 3
    assert h.sleeps == [0.2, 0.4]  # jitter pinned to 1.0: base * 2^attempt


async def test_get_gives_up_after_max_retries():
    h = Harness(sequence(*[httpx.Response(503, json=FAULT)] * 3), max_retries=2)
    with pytest.raises(SimulatorError) as exc:
        await h.client.get("regions")
    assert exc.value.code == "FAULT_INJECTED"
    assert len(h.requests) == 3


async def test_get_retries_timeout_then_reports_it():
    h = Harness(sequence(*[httpx.ReadTimeout("slow")] * 3))
    with pytest.raises(SimulatorError) as exc:
        await h.client.get_health()
    assert exc.value.code == "TIMEOUT"
    assert len(h.requests) == 3


async def test_get_does_not_retry_non_retryable_error():
    h = Harness(sequence(httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": "nope"}})))
    with pytest.raises(SimulatorError) as exc:
        await h.client.get("stations")
    assert (exc.value.code, exc.value.status) == ("NOT_FOUND", 404)
    assert len(h.requests) == 1
    assert h.sleeps == []


async def test_stale_header_is_reported():
    h = Harness(sequence(httpx.Response(200, json={"tick": 1}, headers={"X-Simulator-Stale": "true"})))
    assert (await h.client.get("instance")).stale is True


async def test_422_validation_list_is_normalized():
    body = {"detail": [{"loc": ["body", "quantity"], "msg": "must be > 0"}]}
    h = Harness(sequence(httpx.Response(422, json=body)))
    with pytest.raises(SimulatorError) as exc:
        await h.client.create_allocation({"quantity": 0})
    assert exc.value.code == "VALIDATION_ERROR"
    assert "body.quantity: must be > 0" in exc.value.message


async def test_non_json_error_body():
    h = Harness(sequence(httpx.Response(502, text="bad gateway")))
    with pytest.raises(SimulatorError) as exc:
        await h.client.create_allocation({})
    assert (exc.value.code, exc.value.status) == ("HTTP_ERROR", 502)


async def test_create_allocation_returns_typed_model_and_sends_key():
    h = Harness(sequence(httpx.Response(201, json=ALLOCATION)))
    allocation = await h.client.create_allocation({"idempotency_key": "run:rec:0"})
    assert allocation.id == 7 and allocation.status == "PENDING"
    assert h.requests[0].method == "POST" and h.requests[0].url.path == "/v1/allocations"


async def test_create_allocation_is_never_retried():
    h = Harness(sequence(httpx.Response(503, json=FAULT)))
    with pytest.raises(SimulatorError) as exc:
        await h.client.create_allocation({"idempotency_key": "k"})
    assert exc.value.retryable  # the caller decides, with the same key
    assert len(h.requests) == 1
    assert h.sleeps == []


async def test_create_allocation_conflict_code():
    h = Harness(sequence(httpx.Response(409, json={"detail": {"code": "INSUFFICIENT_INVENTORY", "message": "short"}})))
    with pytest.raises(SimulatorError) as exc:
        await h.client.create_allocation({})
    assert (exc.value.code, exc.value.status, exc.value.retryable) == ("INSUFFICIENT_INVENTORY", 409, False)


async def test_stream_events_yields_parsed_events():
    body = b': connected\n\nevent: simulation.tick\ndata: {"tick": 3}\n\n: keepalive\n\n'
    h = Harness(sequence(httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})))
    events = [e async for e in h.client.stream_events()]
    assert [e.event for e in events] == ["comment", "simulation.tick", "comment"]
    assert events[1].data == {"tick": 3}
    assert h.requests[0].url.path == "/v1/stream"


async def test_stream_connect_failure_is_a_simulator_error():
    h = Harness(sequence(httpx.Response(503, json={"detail": {"code": "FAULT_INJECTED", "message": "stream_disconnect"}})))
    with pytest.raises(SimulatorError) as exc:
        [e async for e in h.client.stream_events()]
    assert exc.value.code == "FAULT_INJECTED" and exc.value.retryable


async def test_stream_transport_error_is_a_simulator_error():
    h = Harness(sequence(httpx.ConnectError("refused")))
    with pytest.raises(SimulatorError) as exc:
        [e async for e in h.client.stream_events()]
    assert exc.value.code == "UNREACHABLE"


async def test_cancel_allocation_typed_and_cannot_cancel():
    h = Harness(sequence(httpx.Response(200, json={**ALLOCATION, "status": "CANCELLED"})))
    assert (await h.client.cancel_allocation(7)).status == "CANCELLED"
    assert h.requests[0].url.path == "/v1/allocations/7/cancel"

    h = Harness(sequence(httpx.Response(409, json={"detail": {"code": "CANNOT_CANCEL", "message": "in transit"}})))
    with pytest.raises(SimulatorError) as exc:
        await h.client.cancel_allocation(7)
    assert exc.value.code == "CANNOT_CANCEL"
