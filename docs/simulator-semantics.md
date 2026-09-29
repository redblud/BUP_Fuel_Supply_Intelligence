# Simulator semantics

Source: `Problem Doc/BUP_Fuel_Supply_Simulator_Integration_Guide_Final (1).pdf`. Every behavior our code relies on is
listed here as **DOCUMENTED** (the guide says so), **VERIFIED** (we observed it against image `1.0.0`, with
how), or **UNVERIFIED** (an assumption). Do not turn an UNVERIFIED line into code without marking the assumption in the code.

## Documented

- Base URL `http://localhost:8000`, Swagger at `/docs`, admin console at `/admin`.
- Tick = 15 simulated minutes (`TICK_MINUTES`); 8 ticks per wall-clock second while RUNNING (`SIMULATION_SPEED`).
- Deterministic: same scenario + seed + actions + injected events → byte-identical state.
- `/v1/health` and `/admin/*` bypass fault injection. Every other `/v1/*` path can be faulted.
- `X-Simulator-Stale: true` on `/v1/*` GETs while a `stale_data` fault is active (not on the SSE stream).
- Errors: domain errors are `{"detail": {"code", "message"}}`; injected faults are `{"error": {"code": "FAULT_INJECTED", ...}}`; `stream_disconnect` returns `{"detail": {"code": "FAULT_INJECTED"}}`.
- `POST /v1/allocations` validation order: idempotency → NOT_FOUND → ROUTE_MISMATCH → DEPOT_CLOSED → STATION_CLOSED → ROUTE_DISRUPTED → ROUTE_CAPACITY_EXCEEDED → INSUFFICIENT_INVENTORY → DISPATCH_CAPACITY_EXCEEDED → DESTINATION_CAPACITY_EXCEEDED.
- Idempotency key lives in the body, 1–150 chars; same key + same body → the existing allocation; same key + different body → 409; cancel does not free the key.
- Cancel is only valid for PENDING; it refunds depot inventory.
- `allocation_failures` counts FAILED allocations (route disrupted at departure time).
- `/v1/demand-history` limit is clamped to [1, 2000] (default 200). 12 rows per tick.
- SSE events: `simulation.tick`, `allocation.status_changed`, `inventory.updated`, `simulator.notice`. There is no replay; a queue of more than 200 buffered events gets you silently dropped; keepalive every 15 s.
- `/admin/reset` wipes everything and reloads the scenario; publishes `simulator.notice` "Simulation reset".
- Event effects: see guide §7.8 (`shipment_delay` and `supply_shortfall` are one-shot and not undone).

## Verified experimentally

_Nothing yet. Dev 1 owns filling this in (issue: "Characterize the simulator")._

## Unverified

### Inventory reservation timing
Status: UNVERIFIED. The guide implies depot inventory is deducted at allocation create time (cancel "refunds" it). Planner assumes deduction at create.

### Departure and arrival ticks
Status: UNVERIFIED. Assumed: created at T → departs T+1 → arrives T+1+transit_ticks. Planner uses this for earliest arrival.

### Destination capacity check
Status: UNVERIFIED. The guide says `station.inventory + quantity > capacity` at submit time. Does it count in-flight shipments? Planner conservatively subtracts in-flight.

### Dispatch capacity window
Status: UNVERIFIED. "In-flight + pending from this depot on this tick". Does it count only allocations created this tick?

### Route max semantics
Status: UNVERIFIED. `max_shipment` is per allocation (not per tick)?

### Idempotency replay status
Status: UNVERIFIED. §5.4 says replay returns 201; §9 says 200. Treat both as success.

### Demand-history ordering
Status: UNVERIFIED. Adapter assumes newest first and reverses it.

### Region demand_factor
Status: UNVERIFIED. Forecast multiplies by `region.demand_factor`; calibration absorbs the error if it is wrong.

### Reset identity
Status: UNVERIFIED. After reset, do allocation ids restart at 1? `run_id` is currently `scenario:seed`, so a reset is not yet detected.
