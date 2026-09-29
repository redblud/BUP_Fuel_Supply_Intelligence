"""SSE parsing and StateSync: events are hints, REST is truth; fallback polling; reconnect with backoff and resync."""

import asyncio
from collections.abc import Callable

import pytest

from app.simulator.errors import SimulatorError
from app.simulator.sse import SseEvent, is_reset_notice, parse_sse_lines, tick_of
from app.state.service import StateService
from app.state.sync import StateSync
from tests.helpers import ScriptedClient

# ---- parser ----------------------------------------------------------------------------------------------------------


async def parse(lines: list[str]) -> list[SseEvent]:
    async def source():
        for line in lines:
            yield line

    return [event async for event in parse_sse_lines(source())]


async def test_parser_handles_comments_events_and_crlf():
    events = await parse(
        [
            ": connected\n",
            "\n",
            "event: simulation.tick\r\n",
            'data: {"tick": 41}\r\n',
            "\r\n",
            ": keepalive\n",
            "event: simulator.notice\n",
            'data: {"message": "Simulation reset"}\n',
            "\n",
        ]
    )
    assert [e.event for e in events] == ["comment", "simulation.tick", "comment", "simulator.notice"]
    assert events[0].data == "connected"
    assert tick_of(events[1]) == 41
    assert is_reset_notice(events[3]) and not is_reset_notice(events[1])


async def test_parser_joins_multiline_data_and_passes_through_bad_json():
    events = await parse(["event: x\n", "data: line one\n", "data: line two\n", "\n", "event: y\n", "data: {broken\n", "\n"])
    assert events[0].data == "line one\nline two"
    assert events[1].data == "{broken"


async def test_parser_ignores_blank_lines_without_data_and_unknown_fields():
    assert await parse(["\n", "id: 7\n", "retry: 1000\n", "\n"]) == []


def test_tick_of_ignores_other_events_and_malformed_payloads():
    assert tick_of(SseEvent("inventory.updated", {"tick": 5})) is None
    assert tick_of(SseEvent("simulation.tick", "oops")) is None
    assert tick_of(SseEvent("simulation.tick", {"tick": "5"})) is None


# ---- StateSync -------------------------------------------------------------------------------------------------------


async def wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(poll(), timeout)
    except TimeoutError:
        pytest.fail("condition not met in time")


async def test_change_event_triggers_a_rest_refresh():
    client = ScriptedClient()
    client.stream_queue = asyncio.Queue()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, fallback_interval=30.0, min_refresh_gap=0.0)
    sync.start()
    try:
        await client.stream_queue.put(SseEvent("comment", "connected"))
        await wait_for(lambda: svc.sse_status == "connected" and svc.latest is not None)
        assert svc.latest.run.tick == 40

        client.advance(41)
        await client.stream_queue.put(SseEvent("simulation.tick", {"tick": 41}))
        await wait_for(lambda: svc.latest.run.tick == 41)
        assert svc.latest_sse_tick == 41
        assert svc.latest.meta.latest_sse_tick == 41
    finally:
        await sync.stop()


async def test_event_payload_is_never_used_as_state():
    """The tick event says 99, but REST says 41: the snapshot follows REST."""
    client = ScriptedClient()
    client.stream_queue = asyncio.Queue()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, fallback_interval=30.0, min_refresh_gap=0.0)
    sync.start()
    try:
        await client.stream_queue.put(SseEvent("comment", "connected"))
        await wait_for(lambda: svc.latest is not None)
        client.advance(41)
        await client.stream_queue.put(SseEvent("simulation.tick", {"tick": 99}))
        await wait_for(lambda: svc.latest.run.tick == 41)
        assert svc.latest_sse_tick == 99
    finally:
        await sync.stop()


async def test_snapshot_serves_the_cache_without_reading_the_simulator():
    client = ScriptedClient()
    client.stream_queue = asyncio.Queue()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, fallback_interval=30.0, min_refresh_gap=0.0)
    sync.start()
    try:
        await wait_for(lambda: svc.latest is not None)
        await asyncio.sleep(0.05)
        reads = sum(client.reads.values())
        for _ in range(5):
            assert (await svc.snapshot()) is svc.latest
        assert sum(client.reads.values()) == reads
    finally:
        await sync.stop()


async def test_reconnects_with_backoff_and_resyncs_over_rest():
    client = ScriptedClient()
    client.stream_queue = asyncio.Queue()
    client.stream_scripts = [SimulatorError("FAULT_INJECTED", "stream_disconnect", 503)]
    svc = StateService(client, tick_tolerance=1, fixture=False)
    gate = asyncio.Event()
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        if seconds > 0:  # backoff: hold the listener so the DEGRADED window is observable
            delays.append(seconds)
            await gate.wait()
        else:
            await asyncio.sleep(0)

    sync = StateSync(svc, fallback_interval=0.05, min_refresh_gap=0.0, backoff_base=0.5, backoff_max=2.0, sleep=sleep, jitter=lambda: 1.0)
    sync.start()
    try:
        await wait_for(lambda: svc.sse_status == "reconnecting")
        assert delays == [0.5]
        client.advance(45)  # missed while the stream was down; the slow REST poll catches up without SSE
        await wait_for(lambda: svc.latest is not None and svc.latest.run.tick == 45)
        assert svc.sse_status == "reconnecting"

        gate.set()
        await client.stream_queue.put(SseEvent("comment", "connected"))
        await wait_for(lambda: svc.sse_status == "connected")
        assert client.stream_attempts == 2
    finally:
        gate.set()
        await sync.stop()


async def test_backoff_grows_and_is_capped():
    client = ScriptedClient()
    client.stream_scripts = [SimulatorError("UNREACHABLE", "down")] * 5
    svc = StateService(client, tick_tolerance=1, fixture=False)
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        if seconds > 0:
            delays.append(seconds)
        await asyncio.sleep(0.001)

    sync = StateSync(svc, fallback_interval=30.0, min_refresh_gap=0.0, backoff_base=0.5, backoff_max=2.0, sleep=sleep, jitter=lambda: 1.0)
    sync.start()
    try:
        await wait_for(lambda: len(delays) >= 5)
    finally:
        await sync.stop()
    assert delays[:5] == [0.5, 1.0, 2.0, 2.0, 2.0]


async def test_simulator_reset_notice_starts_a_new_run():
    client = ScriptedClient()
    client.stream_queue = asyncio.Queue()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, fallback_interval=30.0, min_refresh_gap=0.0)
    sync.start()
    try:
        await client.stream_queue.put(SseEvent("comment", "connected"))
        await wait_for(lambda: svc.latest is not None)
        await client.stream_queue.put(SseEvent("simulator.notice", {"message": "Simulation reset"}))
        await wait_for(lambda: svc.latest.meta.run_id.endswith("#1"))
        assert svc.latest.meta.freshness == "RESET_UNCERTAIN"
    finally:
        await sync.stop()


async def test_polling_fallback_keeps_state_current_without_sse():
    client = ScriptedClient()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, sse_enabled=False, fallback_interval=0.03, min_refresh_gap=0.0)
    sync.start()
    try:
        await wait_for(lambda: svc.latest is not None)
        client.advance(50)
        await wait_for(lambda: svc.latest.run.tick == 50)
        assert svc.sse_status == "disabled"
    finally:
        await sync.stop()


async def test_untrusted_or_failed_read_retries_on_the_short_resync_interval():
    client = ScriptedClient()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, sse_enabled=False, fallback_interval=30.0, resync_interval=0.03, min_refresh_gap=0.0)
    client.error = SimulatorError("FAULT_INJECTED", "down", 503)
    sync.start()
    try:
        await wait_for(lambda: svc.needs_resync and svc.last_error is not None)
        client.error = None  # the 30 s poll would never notice within this test; the resync interval does
        await wait_for(lambda: svc.latest is not None and svc.latest.meta.freshness == "FRESH")
        assert not svc.needs_resync
    finally:
        await sync.stop()


async def test_stop_returns_to_on_demand_reads():
    client = ScriptedClient()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    sync = StateSync(svc, sse_enabled=False, fallback_interval=30.0, min_refresh_gap=0.0)
    sync.start()
    await wait_for(lambda: svc.latest is not None)
    await sync.stop()
    assert not svc.sync_active
    client.advance(60)
    assert (await svc.snapshot()).run.tick == 60


async def test_a_crashing_refresh_does_not_kill_the_loop(monkeypatch):
    client = ScriptedClient()
    svc = StateService(client, tick_tolerance=1, fixture=False)
    real_refresh = svc.refresh
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await real_refresh()

    monkeypatch.setattr(svc, "refresh", flaky)
    sync = StateSync(svc, sse_enabled=False, fallback_interval=30.0, resync_interval=0.02, min_refresh_gap=0.0)
    sync.start()
    try:
        await wait_for(lambda: svc.latest is not None)
        assert calls >= 2
    finally:
        await sync.stop()
