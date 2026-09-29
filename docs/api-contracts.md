# API contracts

Source of truth: `apps/api/app/domain/models.py`. It becomes `apps/web/src/api/generated/openapi.json`, which becomes
`schema.d.ts`. CI fails if either generated file is out of date. Browse the live contract at http://localhost:8000/docs.

## Changing a contract

1. Open a PR labelled `contract` that changes `app/domain/models.py` (and routes if needed).
2. Run `make contracts` (regenerates OpenAPI + TS types) and `make fixtures` if simulator shapes changed.
3. Get a review from each affected track. Prefer **adding** optional fields over renaming or removing them.

## Shared types (frozen at `foundation-v1`)

| Type | Producer | Consumers | Meaning |
|---|---|---|---|
| `NetworkState` (+ `SnapshotMeta`) | Dev 1 (state) | Dev 2, Dev 4, UI | One coherent read of the world, with `run_id`, `tick_start/end`, `stale`, `consistent`, `freshness` |
| `Depot`, `Station`, `Route`, `SupplyArrival`, `SimEvent`, `Allocation`, `DemandObservation` | Dev 1 | all | Mirror simulator `/v1/*` field for field |
| `Forecast` | Dev 2 | risk, UI | `liters_per_tick` of **demand** (not served) from `start_tick`, with `calibration` alpha |
| `InventoryProjection` | Dev 2 | risk, UI charts | Inventory per tick incl. incoming shipments, with `safety_stock` |
| `RiskAssessment` | Dev 2 | planner, Tripwire, UI | Level, `reason_codes`, breach/stockout ticks, earliest arrival, `redundancy` |
| `Recommendation` | Dev 2 | Dev 4, UI | Exact `AllocationRequest` + `generated_tick`/`expiry_tick` + explanation, stress margin, alternatives |
| `Plan` | Dev 2 | Dev 4, UI | Everything above for one tick + `planner_version` (`fallback` if the primary planner failed) |
| `TripwireStatus` (`Trip`) | Dev 4 | UI, orchestrator | CLEAR / TRIPPED; each trip has code, severity, scope, detected tick, required actions |
| `SystemHealth` | Dev 4 (+ each owner) | UI | Per component: api, simulator, sse, snapshot, database, forecast, planner, tripwire, execution |
| `AutomationState` | Dev 4 | UI | `mode` ADVISORY / GUARDED_AUTO / MANUAL_DEMO and `kill_switch` |
| `RecommendationState` | Dev 4 | UI | PROPOSED → APPROVED → EXECUTING → DONE, or FAILED / EXPIRED / SUPERSEDED / REJECTED |

## Endpoints

| Method | Path | Returns | Status |
|---|---|---|---|
| GET | `/api/health` | `SystemHealth` | done (simulator + database checks) |
| GET | `/api/health/live` | `{status}` | done (Docker liveness) |
| GET | `/api/state` | `NetworkState` (503 `ErrorBody` if unreadable) | done |
| GET | `/api/plan` | `Plan` | done (baseline planner) |
| GET | `/api/dashboard` | `DashboardResponse`: state, plan, tripwire, health, automation, recommendation states, last trusted tick/age | done; **the UI polls this** |
| GET / PUT | `/api/automation` | `AutomationState` | in-memory; Dev 4 persists + guards |
| POST | `/api/recommendations/{id}/approve` | `RecommendationState` | 501 stub, Dev 4 |
| POST | `/api/recommendations/{id}/reject` | `RecommendationState` | 501 stub, Dev 4 |
| POST | `/api/sim/control` `{action: run\|pause\|step}` | simulator admin response | done (real mode only; demo use) |
| GET | `/api/allocations?status&limit` | `TrackedAllocation[]`: simulator allocations of the current run with observed transitions | done |
| GET | `/api/events?status&limit` | `SimEvent[]` from the latest snapshot, newest start first | done |
| GET | `/api/demand-history?station_id&fuel_type&limit` | `DemandObservation[]`, oldest first, newest `limit` rows (max 2000), from stored history | done |
| GET | `/api/decisions?status&limit` | `RecommendationState[]`, newest first | in-memory until Dev 4 persists the lifecycle |

`/api/dashboard` stays one fast poll: in real mode it serves the snapshot cached by the background sync (SSE hints + REST
truth), never a per-request simulator read. Heavy history goes through the endpoints above. `/api/allocations`,
`/api/events` and `/api/demand-history` answer 503 (`ErrorBody`) when no snapshot exists.

### Snapshot trust (`meta.freshness`)

| Value | Meaning | Trusted |
|---|---|---|
| `FRESH` / `FIXTURE` | Consistent, not stale (or a fixture) | yes |
| `STALE` | Simulator sent `X-Simulator-Stale` | no |
| `TORN` | Tick moved more than `SNAPSHOT_TICK_TOLERANCE` during the read | no |
| `UNAVAILABLE` | Simulator unreadable; this is the **last trusted** snapshot, so `meta.retrieved_at` shows its real age | no |
| `RESET_UNCERTAIN` | A reset was detected (`run_id` gained a `#n` suffix); trusted again after the next clean full resync | no |

`NetworkState.history_gaps` lists demand ticks we never observed (`HISTORY_GAP`); they are never filled with invented
values. `NetworkState.demand_history` merges the simulator window with stored observations
(`DEMAND_HISTORY_TICKS`, default 96 ticks). For the execution gateway, `StateService.allocation_status(allocation_id=…)` /
`(idempotency_key=…)` answers "what is the status of allocation X / key K" from the tracked lifecycle.

Errors use `{"detail": {"code": "UPPER_SNAKE", "message": "..."}}`.

## Fixtures

`tests/fixtures/<scenario>.json` maps simulator resource names (`instance`, `depots`, …, `demand-history`, `metrics`,
`health`) to raw `/v1/*` bodies. `"stale": true` makes every read report `X-Simulator-Stale`. Select one with
`FIXTURE_SCENARIO`. In Python tests, `tests.conftest.load_state("route-disruption")` gives a `NetworkState`.

| Scenario | Tick | What |
|---|---|---|
| `normal` | 0 | Initial world, calm |
| `route-disruption` | 40 | Dhaka demand spike ×1.8, `route-gazipur-mirpur` DISRUPTED, diesel in transit to Tongi, three HIGH risks |
| `stale` | 40 | Same, but stale → Tripwire TRIPPED |

Wanted next (issues): `risk-tongi-diesel`, `allocation-in-transit`, `reset-recovery`, `scarcity` (two stations competing
for one depot), and real recordings from the simulator.
