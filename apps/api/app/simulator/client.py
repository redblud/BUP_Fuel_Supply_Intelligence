"""SimulatorClient interface and the real HTTP implementation. Owner: Developer 1.

This is the only code that knows raw simulator URLs. Everything above it works with
NetworkState (see app/simulator/mapper.py and app/state/reconciler.py).

TODO(dev1): bounded retries with backoff, SSE listener (sse.py), cancel_allocation.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.simulator.errors import SimulatorError

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

    async def create_allocation(self, body: dict) -> dict: ...

    async def admin(self, action: str) -> dict: ...

    async def close(self) -> None: ...


def _error_from(response: httpx.Response) -> SimulatorError:
    try:
        payload = response.json()
    except ValueError:
        return SimulatorError("HTTP_ERROR", response.text[:200], response.status_code)
    err = (payload.get("error") or payload.get("detail")) if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return SimulatorError(err.get("code", "HTTP_ERROR"), err.get("message", ""), response.status_code)
    code = "VALIDATION_ERROR" if response.status_code == 422 else "HTTP_ERROR"
    return SimulatorError(code, str(err), response.status_code)


class RealSimulatorClient:
    def __init__(self, base_url: str, connect_timeout: float, read_timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

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

    async def get_health(self) -> dict:
        return (await self._request("GET", "/v1/health")).json()

    async def get(self, resource: str, **params: Any) -> SimResponse:
        response = await self._request("GET", RESOURCES[resource], params=params or None)
        return SimResponse(response.json(), response.headers.get("X-Simulator-Stale", "").lower() == "true")

    async def create_allocation(self, body: dict) -> dict:
        """POST /v1/allocations. `body` must include idempotency_key. Retry with the SAME key and body."""
        return (await self._request("POST", "/v1/allocations", json=body)).json()

    async def admin(self, action: str) -> dict:
        """Demo/test controls only (run | pause | step | reset). Never used by planning logic."""
        return (await self._request("POST", f"/admin/{action}")).json()

    async def close(self) -> None:
        await self._http.aclose()
