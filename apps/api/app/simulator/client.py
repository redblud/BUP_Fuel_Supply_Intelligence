"""SimulatorClient interface and the real HTTP implementation. Owner: Developer 1.

This is the only code that knows raw simulator URLs. Everything above it works with
NetworkState (see app/simulator/mapper.py and app/state/reconciler.py).

GETs are retried with jittered backoff when the failure is retryable. POST and admin calls are
never retried here: the execution gateway owns the retry policy and must reuse the same idempotency key.

The SSE stream is opened with `stream_events()`; `StateSync` (app/state/sync.py) consumes it.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.domain.models import Allocation
from app.simulator.errors import SimulatorError
from app.simulator.sse import SseEvent, parse_sse_lines

# Simulator resource name -> path. Keys are also the fixture keys in tests/fixtures/*.json.
RESOURCES = {
    "instance": "/v1/instance",
    "regions": "/v1/regions",
    "depots": "/v1/depots",
    "stations": "/v1/stations",
    "routes": "/v1/routes",
    "supply-arrivals": "/v1/supply-arrivals",
    "events": "/v1/events",
    "allocations": "/v1/allocations",
    "demand-history": "/v1/demand-history",
    "metrics": "/v1/metrics",
}


@dataclass
class SimResponse:
    body: Any
    stale: bool  # X-Simulator-Stale: true


class SimulatorClient(Protocol):
    async def get_health(self) -> dict: ...

    async def get(self, resource: str, **params: Any) -> SimResponse: ...

    async def create_allocation(self, body: dict) -> Allocation: ...

    async def cancel_allocation(self, allocation_id: int) -> Allocation: ...

    async def admin(self, action: str) -> dict: ...

    def stream_events(self) -> AsyncIterator[SseEvent]: ...

    async def close(self) -> None: ...


def _error_from(response: httpx.Response) -> SimulatorError:
    """Normalize {"detail": {...}}, {"error": {...}}, FastAPI 422 lists, and non-JSON bodies into SimulatorError."""
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        return SimulatorError("HTTP_ERROR", response.text[:200], status)
    err = (payload.get("error") or payload.get("detail")) if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return SimulatorError(str(err.get("code", "HTTP_ERROR")), str(err.get("message", "")), status)
    if isinstance(err, list):  # 422: [{"loc": [...], "msg": "..."}]
        parts = [f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg', '')}" for item in err if isinstance(item, dict)]
        return SimulatorError("VALIDATION_ERROR", "; ".join(parts), status)
    code = "VALIDATION_ERROR" if status == 422 else "HTTP_ERROR"
    return SimulatorError(code, str(err) if err is not None else response.text[:200], status)


class RealSimulatorClient:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
        backoff_base: float = 0.2,
        backoff_max: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        stream_read_timeout: float = 45.0,
    ) -> None:
        """`max_retries` counts retries after the first attempt (GET only). Delay is full jitter: jitter() * min(max, base * 2^n)."""
        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._jitter = jitter
        self._stream_timeout = httpx.Timeout(stream_read_timeout, connect=connect_timeout)  # keepalive arrives every 15 s

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SimulatorError("TIMEOUT", str(exc)) from exc
        except httpx.TransportError as exc:
            raise SimulatorError("UNREACHABLE", str(exc)) from exc
        if response.status_code >= 400:
            raise _error_from(response)
        return response

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        """GET with bounded retries on retryable SimulatorError; the last error is re-raised."""
        attempt = 0
        while True:
            try:
                return await self._request("GET", path, **kwargs)
            except SimulatorError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                await self._sleep(self._jitter() * min(self._backoff_max, self._backoff_base * 2**attempt))
                attempt += 1

    async def get_health(self) -> dict:
        return (await self._get("/v1/health")).json()

    async def get(self, resource: str, **params: Any) -> SimResponse:
        response = await self._get(RESOURCES[resource], params=params or None)
        return SimResponse(response.json(), response.headers.get("X-Simulator-Stale", "").lower() == "true")

    async def create_allocation(self, body: dict) -> Allocation:
        """POST /v1/allocations. `body` must include idempotency_key. Never retried here: retry with the SAME key and body."""
        return Allocation.model_validate((await self._request("POST", "/v1/allocations", json=body)).json())

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        """POST /v1/allocations/{id}/cancel. Only PENDING allocations can be cancelled (409 CANNOT_CANCEL otherwise)."""
        return Allocation.model_validate((await self._request("POST", f"/v1/allocations/{allocation_id}/cancel")).json())

    async def admin(self, action: str) -> dict:
        """Demo/test controls only (run | pause | step | reset). Never used by planning logic."""
        return (await self._request("POST", f"/admin/{action}")).json()

    async def stream_events(self) -> AsyncIterator[SseEvent]:
        """Open `/v1/stream` and yield events until it ends. Raises SimulatorError if it cannot connect (e.g. a stream_disconnect
        fault answers 503) or drops mid-stream; the caller reconnects with backoff and then does a full REST resync."""
        try:
            async with self._http.stream("GET", "/v1/stream", timeout=self._stream_timeout) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise _error_from(response)
                async for event in parse_sse_lines(response.aiter_lines()):
                    yield event
        except httpx.TimeoutException as exc:
            raise SimulatorError("TIMEOUT", str(exc)) from exc
        except httpx.TransportError as exc:
            raise SimulatorError("UNREACHABLE", str(exc)) from exc

    async def close(self) -> None:
        await self._http.aclose()
