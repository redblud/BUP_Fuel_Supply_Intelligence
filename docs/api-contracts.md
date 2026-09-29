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
| `AutomationState` | Dev 4 | UI | `mode`, `kill_switch`, guarded-auto envelope (`max_quantity_per_allocation`, `max_liters_per_hour`, `allowed_fuels`, `ttl_minutes`, `mode_expires_at`) |
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
| POST | `/api/recommendations/{id}/approve` | `RecommendationState` | authenticated, freshness/tripwire validated, idempotent execution |
| POST | `/api/recommendations/{id}/reject` | `RecommendationState` | authenticated, durable lifecycle update |
| POST | `/api/sim/control` `{action: run\|pause\|step}` | simulator admin response | authenticated control path |

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

### Dev 4 additive contract extensions

- `RunInfo.id` is an additive simulator-instance identity field used for reset detection.
- `Trip.classification` is additive and may be `HARD`, `SOFT`, or `INFO`.
- `TripCode` includes `KILL_SWITCH_ON` and `SSE_DISCONNECTED`.
- `AutomationState` includes guarded-auto envelope fields and optional `mode_expires_at`.
- Protected operator mutation endpoints use `Authorization: Bearer <OPERATOR_TOKEN>`.

### Human-readable explanation extension (Issue #10)

`Recommendation` already carries the operator-facing explanation fields required by the intelligence track:
`summary`, `factors`, `reason_codes`, `stress_margin_liters`, and `alternatives[].rejected_because`.

`Plan.blocked_cases` is an additive extension. Each `BlockedCase` explains a risk for which the planner could
not form a safe executable shipment. Blocker codes are deterministic: `STATION_UNREACHABLE`, `STATION_OUTAGE`,
`DEPOT_BELOW_RESERVE`, `DISPATCH_CAPACITY_FULL`, `STATION_CAPACITY_FULL`, `MIN_SHIPMENT`, and `ALREADY_COVERED`.
The dashboard renders the summary, factors, reason codes, stress margin, alternatives, and blocked cases directly.

### Recommendation impact/confidence extension (Issue #27)

`Recommendation` additionally carries deterministic impact estimates comparing the recommended shipment with no shipment over the forecast horizon:
`projected_stockout_ticks_avoided`, `projected_unmet_demand_liters_avoided`, `inventory_at_arrival_without_liters`, and `inventory_at_arrival_with_liters`.

Measured forecast-error fields are `forecast_error_mae_liters_per_tick` and `forecast_error_band_liters_per_tick`.
`confidence` is a qualitative robustness label (`HIGH`, `MEDIUM`, `LOW`, or `INSUFFICIENT_DATA`) derived only from the measured recent error band and stress margin.
`confidence_basis` gives the non-probabilistic explanation. No uncalibrated probability is exposed.
