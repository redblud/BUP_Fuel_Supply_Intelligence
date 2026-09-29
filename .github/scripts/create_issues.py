"""Create the labels, milestone, and track issues on GitHub. Idempotent: skips issues whose title exists.

    gh auth login          # once
    python .github/scripts/create_issues.py [--repo owner/name]

Uses the `gh` CLI (PATH, or GH_BIN=path/to/gh).
"""

import json
import os
import subprocess
import sys

REPO = sys.argv[sys.argv.index("--repo") + 1] if "--repo" in sys.argv else "redblud/BUP_Fuel_Supply_Intelligence"
GH = os.environ.get("GH_BIN", "gh")
MILESTONE = "2h build"

LABELS = {
    "backend": ("1d76db", "Dev 1: simulator adapter, state, persistence"),
    "ai": ("8e44ad", "Dev 2: forecast, topology, risk, planner"),
    "frontend": ("0e8a16", "Dev 3: React operator console"),
    "reliability": ("d93f0b", "Dev 4: tripwire, decisions, execution, CI, infra"),
    "contract": ("fbca04", "Changes app/domain/models.py or the API; needs cross-track review"),
    "P0": ("b60205", "Needed for the live demo gate"),
    "P1": ("c5def5", "After the demo gate works"),
}

COMMON = "\n\n---\nContracts: `apps/api/app/domain/models.py` · docs: `docs/api-contracts.md`, `docs/development.md`. Work on your track branch, PR into `staging`."

ISSUES: list[tuple[str, list[str], str]] = [
    # ---------------------------------------------------------------- contract
    ("Contract freeze foundation-v1: read before coding", ["contract", "P0"], """
Everyone works against the frozen shared types in `apps/api/app/domain/models.py`:
`NetworkState`/`SnapshotMeta`, `Forecast`, `InventoryProjection`, `RiskAssessment`, `Recommendation`, `Plan`,
`TripwireStatus`, `SystemHealth`, `AutomationState`, `RecommendationState`, `DashboardResponse`.

- [ ] Dev 1 read and agrees
- [ ] Dev 2 read and agrees
- [ ] Dev 3 read and agrees
- [ ] Dev 4 read and agrees

Changes after this: PR labelled `contract`, run `make contracts`, prefer adding optional fields."""),
    # ---------------------------------------------------------------- backend (Dev 1)
    ("[backend] Run the real simulator and characterize its semantics", ["backend", "P0"], """
Run `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (`make up-sim`, host port 9000). Use `/admin/step` while paused.

- [ ] Record real `/v1/*` responses into a fixture (`tests/fixtures/recorded-tick-N.json`, same format as `route-disruption.json`)
- [ ] Verify each UNVERIFIED item in `docs/simulator-semantics.md` (arrival tick, reservation timing, destination capacity, dispatch window, demand-history order, replay status) and move it to "Verified" with how
- [ ] Note anything that contradicts the planner's assumptions in the Dev 2 issue"""),
    ("[backend] Live NetworkState from RealSimulatorClient", ["backend", "P0"], """
`SIMULATOR_MODE=real` must give a correct `NetworkState` via `app/state/reconciler.py`.

- [ ] Mapper validates real responses (fix any shape drift in `domain/models.py` via a contract PR)
- [ ] TORN when tick moves more than `SNAPSHOT_TICK_TOLERANCE` during a read; STALE on `X-Simulator-Stale`
- [ ] Test with `httpx.MockTransport` for 503 `FAULT_INJECTED` (`error` shape) and 409 (`detail` shape)
- [ ] `/api/health` simulator component shows real tick and status"""),
    ("[backend] Background state poller + SSE change hints", ["backend", "P0"], """
Replace read-on-request in `app/state/service.py`.

- [ ] Background task refreshes REST on each `simulation.tick` / `allocation.status_changed` SSE event, plus a slow fallback poll
- [ ] `app/simulator/sse.py`: reconnect with backoff; 503 on `stream_disconnect` → retry; on reconnect do a full REST resync
- [ ] `/api/dashboard` serves the cached latest snapshot (fast), `latest_sse_tick` filled in `SnapshotMeta`
- [ ] `sse` component in `SystemHealth`: CONNECTED → HEALTHY, reconnecting → DEGRADED"""),
    ("[backend] Reset detection and run_id", ["backend", "P1"], """
- [ ] Detect reset: tick or sim_time regression, allocation id regression, `simulator.notice` "Simulation reset"
- [ ] New `run_id` on reset; freshness `RESET_UNCERTAIN` until a full resync completes
- [ ] Tests with two fixtures (tick 40 then tick 0)"""),
    ("[backend] Persist demand observations and snapshots to SQLite", ["backend", "P1"], """
- [ ] Alembic set up in `apps/api` (replace `create_all`)
- [ ] Tables `simulation_runs`, `demand_observations` keyed by (run_id, station, fuel, tick), `snapshots` (last trusted)
- [ ] Planner receives longer history than the 200-row REST window
- [ ] Data survives `docker compose restart backend`"""),
    ("[backend] Bounded retries, timeouts and last-trusted fallback", ["backend", "P1"], """
- [ ] Retry GETs on `SimulatorError.retryable` with jittered backoff (max ~3 tries / 2 s total)
- [ ] Under `latency` and `error_rate` faults the dashboard keeps showing the last trusted state with its age, never labelled FRESH
- [ ] Never retry a POST with a new idempotency key"""),
    # ---------------------------------------------------------------- ai (Dev 2)
    ("[ai] Forecast calibration that survives demand spikes + forecast error metric", ["ai", "P0"], """
`app/intelligence/forecast.py`. Today alpha compares past observations with *current* `demand_multiplier`, so it is skewed
right after a spike starts.

- [ ] Calibrate per (station, fuel) using the multiplier in force at each observed tick (from `events`), or a robust ratio
- [ ] Target is `demand_liters`, not `served_liters`
- [ ] Add forecast error (e.g. MAPE over the last N ticks) to `Forecast` via a contract PR, and show it in health
- [ ] Tests on `route-disruption` and `normal` fixtures"""),
    ("[ai] Scarcity-aware order-up-to planner", ["ai", "P0"], """
`app/intelligence/planner.py`.

- [ ] New fixture `tests/fixtures/scarcity.json`: two stations need the same fuel from one depot with insufficient stock/dispatch
- [ ] Allocation order: earliest breach first, lower redundancy second, then balance resulting cover (hours)
- [ ] `SCARCITY_LIMITED` reason + alternatives explain who lost out and why
- [ ] Split quantities above `max_shipment` into multiple recommendations when dispatch capacity allows
- [ ] Respect depot reserve; stays deterministic (test: same input → same `Plan`)"""),
    ("[ai] Explanations and CONNECTIVITY_RISK vs scarcity", ["ai", "P1"], """
- [ ] Every recommendation has a clear `summary`, factors, `stress_margin_liters`, alternatives with `rejected_because`
- [ ] Unreachable stations (all routes disrupted / station outage) are `CONNECTIVITY_RISK` and produce no recommendation, with a clear reason
- [ ] Unit tests for topology with disrupted routes"""),
    ("[ai] Benchmark runner: do-nothing vs order-up-to", ["ai", "P1"], """
`benchmark/`: drive a separate simulator instance with `/admin/reset` + `/admin/step`, apply `build_plan` each tick,
record `/v1/metrics`.

- [ ] Compare do-nothing vs baseline on the same seed over N ticks; include one crisis (demand spike + route disruption)
- [ ] Output a small markdown table: service level, unmet liters, allocation failures, shipments, planner runtime"""),
    # ---------------------------------------------------------------- frontend (Dev 3)
    ("[frontend] Operator console layout: header, priority risks, current plan", ["frontend", "P0"], """
Grow `apps/web/src/pages/OperationsDashboard.tsx` into feature folders (`src/features/*`). Data: `GET /api/dashboard` (already polled every 2 s).

- [ ] Header: tick, sim time, freshness, mode, tripwire, kill switch state
- [ ] Priority risks list from `plan.risks` (level, reason codes, breach/stockout ticks, earliest arrival)
- [ ] Current plan: each `Recommendation` with quantity, route, arrival, why (`factors`), stress margin, alternatives
- [ ] Works with backend in fake mode (`FIXTURE_SCENARIO=route-disruption`)"""),
    ("[frontend] Inventory projection chart", ["frontend", "P0"], """
Recharts line chart per station/fuel from `plan.projections`: inventory over ticks, safety-stock line, incoming shipment markers,
and the recommendation's expected arrival. Select a station/fuel from the risk list."""),
    ("[frontend] Degraded and untrusted states + system health panel", ["frontend", "P0"], """
- [ ] Banner when `state.meta.freshness` is not FRESH/FIXTURE or `tripwire.state == TRIPPED`: "SIMULATOR DATA UNTRUSTED · last trusted tick N · age Xs · automatic execution paused"
- [ ] `state == null` renders without crashing (test with `FIXTURE_SCENARIO=does-not-exist`)
- [ ] Health panel from `health.components` (api, simulator, sse, snapshot, database, forecast, planner, tripwire, execution)
- [ ] Check with `FIXTURE_SCENARIO=stale`"""),
    ("[frontend] Network, supplies, events and allocations panels", ["frontend", "P1"], """
- [ ] Network: depots → routes → stations, disrupted routes highlighted (simple SVG/CSS; no graph library)
- [ ] Supplies (`supply_arrivals`), events (`events`, ACTIVE highlighted), allocations with status progress"""),
    ("[frontend] Operator controls: approve/reject, mode, kill switch, sim controls", ["frontend", "P1"], """
- [ ] Approve / reject buttons → `POST /api/recommendations/{id}/approve|reject`; show `RecommendationState`; handle 501/409 gracefully
- [ ] Mode selector + kill switch → `PUT /api/automation`
- [ ] Demo controls → `POST /api/sim/control` (run / pause / step)"""),
    # ---------------------------------------------------------------- reliability (Dev 4)
    ("[reliability] Complete Tripwire rules", ["reliability", "P0"], """
`app/safety/tripwire.py`, deterministic.

- [ ] Add RESET_UNCERTAIN, RUN_ID_UNKNOWN, PERSISTENCE_FAILURE, EXECUTION_UNKNOWN, RECOMMENDATION_EXPIRED (per recommendation), FORECAST_DRIFT
- [ ] Each trip: code, severity, scope, detected tick, required actions
- [ ] Unit test per rule using fixtures (`stale`, missing fixture, etc.)"""),
    ("[reliability] Supervised execution: approve → validate → idempotent POST", ["reliability", "P0", "contract"], """
`app/decision/` + `/api/recommendations/{id}/approve`.

- [ ] Lifecycle PROPOSED → APPROVED → EXECUTING → DONE / FAILED / EXPIRED / SUPERSEDED / REJECTED, persisted in SQLite
- [ ] Before POST: fresh snapshot, Tripwire CLEAR, not expired, re-check route/station/depot/inventory/dispatch/destination capacity
- [ ] Persist intent + idempotency key (`{run_id}:{rec_id}:{n}`) as PREPARED before POST; retry uses the same key + body
- [ ] Per-depot asyncio lock around refresh → validate → POST
- [ ] Track the simulator allocation to ARRIVED/FAILED via state refresh"""),
    ("[reliability] Operating modes, kill switch and Guarded Auto loop", ["reliability", "P1"], """
- [ ] Persist `AutomationState`; switching to GUARDED_AUTO is refused while TRIPPED
- [ ] Guarded Auto: each tick, execute PROPOSED recommendations through the same validated path; kill switch overrides everything
- [ ] Every automatic action is audited (`system_events`)"""),
    ("[reliability] Clean-clone Docker run, CI green, branch protection", ["reliability", "P0"], """
- [ ] `git clone && cp .env.example .env && docker compose up --build` works; frontend reaches backend; DB survives restart
- [ ] `make up-sim` + `SIMULATOR_MODE=real` works against the published image
- [ ] CI green on `staging`; protect `main` and `staging` (PR + green CI)"""),
    ("[reliability] Fault scenario tests", ["reliability", "P1"], """
`tests/scenarios/` (or `apps/api/tests/scenarios`): a fake client that injects faults.

- [ ] unavailable (503) → UNAVAILABLE + SIMULATOR_UNAVAILABLE trip, UI state null
- [ ] error_rate → retries succeed, no false FRESH
- [ ] stale_data → STALE + TRIPPED; torn read (tick changes mid-read) → TORN
- [ ] Ambiguous POST (timeout after send) → retry with same key does not duplicate"""),
]


def gh(*args: str, check: bool = True) -> str:
    result = subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def main() -> None:
    gh("auth", "status")
    for name, (color, desc) in LABELS.items():
        gh("label", "create", name, "--repo", REPO, "--color", color, "--description", desc, "--force")

    milestones = json.loads(gh("api", f"repos/{REPO}/milestones?state=all"))
    if not any(m["title"] == MILESTONE for m in milestones):
        gh("api", f"repos/{REPO}/milestones", "-f", f"title={MILESTONE}")

    existing = {i["title"] for i in json.loads(gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "500", "--json", "title"))}
    for title, labels, body in ISSUES:
        if title in existing:
            print(f"skip   {title}")
            continue
        url = gh("issue", "create", "--repo", REPO, "--title", title, "--body", body.strip() + COMMON,
                 "--label", ",".join(labels), "--milestone", MILESTONE).strip()
        print(f"create {url}")


if __name__ == "__main__":
    main()
