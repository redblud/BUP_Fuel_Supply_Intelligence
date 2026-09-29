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
| `Forecast` (+ `ForecastError`) | Dev 2 | risk, UI | `liters_per_tick` of **demand** (not served) from `start_tick`, with `calibration` alpha and measured rolling MAE/RMSE/WAPE/sMAPE |
| `DepotSupplyProjection` | Dev 2 | planner, UI | Expected depot stock by tick after scheduled arrivals, delays, and supply shortfalls |
| `InventoryProjection` | Dev 2 | risk, UI charts | Inventory, demand, incoming supply, and unmet demand per tick, with cumulative shortage and minimum inventory |
| `RiskAssessment` (+ `RiskDriver`) | Dev 2 | planner, Tripwire, UI | Severity, structured numerical drivers, coverage, shortage, breach/stockout timing, earliest feasible arrival, and route redundancy |
| `Recommendation` | Dev 2 | Dev 4, UI | Exact `AllocationRequest` + `generated_tick`/`expiry_tick` + explanation, stress margin, alternatives; one order-up-to target may produce multiple route-limit-safe recommendations |
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

Errors use `{"detail": {"code": "UPPER_SNAKE", "message": "..."}}`.

## Fixtures

`tests/fixtures/<scenario>.json` maps simulator resource names (`instance`, `depots`, …, `demand-history`, `metrics`,
`health`) to raw `/v1/*` bodies. `"stale": true` makes every read report `X-Simulator-Stale`. Select one with
`FIXTURE_SCENARIO`. In Python tests, `tests.conftest.load_state("route-disruption")` gives a `NetworkState`.

| Scenario | Tick | What |
|---|---|---|
| `normal` | 0 | Initial world, calm |
| `demand-spike` | 24 | Dhaka spike starts after a baseline calibration window; used to compare event-aware and naive forecasts |
| `scarcity` | 0 | Mirpur and Tongi compete for one depot's insufficient reserve-adjusted diesel and dispatch capacity |
| `route-disruption` | 40 | Dhaka demand spike ×1.8, `route-gazipur-mirpur` DISRUPTED, diesel in transit to Tongi, three HIGH risks |
| `stale` | 40 | Same, but stale → Tripwire TRIPPED |

Wanted next (issues): `risk-tongi-diesel`, `allocation-in-transit`, `reset-recovery`, and real recordings from the simulator.
