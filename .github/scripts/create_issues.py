"""Sync labels, milestone, and track issues on GitHub. Safe to re-run.

Issues with a number are edited in place (title, body, labels); issues without one are
created unless an issue with the same title already exists.

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
    "backend": ("1d76db", "Dev 1: simulator integration, state, persistence, backend APIs"),
    "ai": ("8e44ad", "Dev 2: forecast, risk, planner, explanations, benchmark"),
    "frontend": ("0e8a16", "Dev 3: React operator console"),
    "reliability": ("d93f0b", "Dev 4: tripwires, execution, modes, faults, observability, DevOps"),
    "contract": ("fbca04", "Changes app/domain/models.py or the API; needs cross-track review"),
    "P0": ("b60205", "Needed for the live demo gate"),
    "P1": ("c5def5", "After the demo gate works"),
}
PRIORITIES = {"P0", "P1"}

COMMON = (
    "\n\n---\nContracts: `apps/api/app/domain/models.py` (NetworkState = the canonical world state) · "
    "docs: `docs/api-contracts.md`, `docs/development.md`. Work on your track branch, PR into `staging`."
)

# (existing issue number or None, title, labels, body)
ISSUES: list[tuple[int | None, str, list[str], str]] = [
    # ================================================================ CONTRACT / EVERYONE
    (1, "Contract freeze foundation-v1: read before coding", ["contract", "P0"], """
Everyone works against the frozen shared types in `apps/api/app/domain/models.py`:
`NetworkState`/`SnapshotMeta`, `Forecast`, `InventoryProjection`, `RiskAssessment`, `Recommendation`, `Plan`,
`TripwireStatus`, `SystemHealth`, `AutomationState`, `RecommendationState`, `DashboardResponse`.

Ownership boundary to avoid duplicate work:
- **Simulator allocation status** (PENDING → IN_TRANSIT → ARRIVED/FAILED/CANCELLED, as the simulator reports it) is Dev 1.
- **Recommendation lifecycle + submitting allocations** (approve, validate, idempotent POST, retry) is Dev 4.

- [ ] Dev 1 read and agrees
- [ ] Dev 2 read and agrees
- [ ] Dev 3 read and agrees
- [ ] Dev 4 read and agrees

Changes after this: PR labelled `contract`, run `make contracts`, prefer adding optional fields."""),

    # ================================================================ DEV 1 — BACKEND / INTEGRATION
    (2, "[backend] Simulator contract tests + recorder", ["backend", "P0"], """
Run `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (`make up-sim`, host port 9000). Drive with `/admin/step` while paused.

- [ ] Recorder script: dump every `/v1/*` resource into `tests/fixtures/recorded-<name>.json` (same format as `route-disruption.json`)
- [ ] Contract tests: recorded responses validate against `app/domain/models.py`; failures show exactly which field drifted
- [ ] Verify each UNVERIFIED item in `docs/simulator-semantics.md` (arrival tick, reservation timing, destination capacity, dispatch window, demand-history order, replay status); move to "Verified" with how
- [ ] Tell Dev 2 about anything that contradicts planner assumptions"""),
    (None, "[backend] Hardened simulator REST client", ["backend", "P0"], """
`app/simulator/client.py` (`RealSimulatorClient`).

- [ ] Separate connect/read timeouts from settings; bounded retries with jittered backoff for GETs on `SimulatorError.retryable`
- [ ] Both error shapes normalized (`{"detail":{...}}` and `{"error":{...}}`), 422 validation errors included
- [ ] `create_allocation` and `cancel_allocation` typed; POST never retried with a new key (Dev 4 owns retry policy)
- [ ] Unit tests with `httpx.MockTransport`: 503 FAULT_INJECTED, 409 codes, timeout, stale header"""),
    (3, "[backend] Canonical world state (NetworkState) reconciler", ["backend", "P0"], """
`SIMULATOR_MODE=real` must produce a correct, coherent `NetworkState` via `app/state/reconciler.py`.

- [ ] instance → parallel reads → instance; TORN when tick moves more than `SNAPSHOT_TICK_TOLERANCE`; STALE on `X-Simulator-Stale`
- [ ] Mapper handles real responses (shape fixes go through a contract PR)
- [ ] Last trusted snapshot kept; untrusted data is never labelled FRESH
- [ ] `/api/health` simulator component shows real tick and status"""),
    (4, "[backend] SSE + polling synchronization", ["backend", "P0"], """
Replace read-on-request in `app/state/service.py`.

- [ ] `app/simulator/sse.py`: listen to `/v1/stream`; `simulation.tick` / `allocation.status_changed` / `inventory.updated` trigger a REST refresh (SSE is a hint, REST is truth)
- [ ] Slow fallback poll when SSE is silent or down; reconnect with backoff; 503 on `stream_disconnect` → retry; full REST resync after reconnect
- [ ] `/api/dashboard` serves the cached latest snapshot; `latest_sse_tick` filled in `SnapshotMeta`
- [ ] `sse` component in `SystemHealth`: connected → HEALTHY, reconnecting → DEGRADED"""),
    (5, "[backend] Reset / run identity detection", ["backend", "P1"], """
- [ ] Detect reset: tick or sim_time regression, allocation id regression, `simulator.notice` "Simulation reset"
- [ ] New `run_id` on reset; freshness `RESET_UNCERTAIN` until a full resync completes
- [ ] Identity everywhere is (run_id, allocation_id)
- [ ] Tests with two fixtures (tick 40 then tick 0)"""),
    (6, "[backend] Persistence + historical state", ["backend", "P1"], """
- [ ] Alembic set up in `apps/api` (replace `create_all`)
- [ ] Tables: `simulation_runs`, `demand_observations` (run_id, station, fuel, tick), `snapshots` (last trusted), `station_state_observations`
- [ ] Planner receives longer demand history than the 200-row REST window
- [ ] Data survives `docker compose restart backend`"""),
    (None, "[backend] Allocation lifecycle tracking (simulator side)", ["backend", "P1"], """
Observe, don't submit (submission is Dev 4's execution gateway).

- [ ] Track every simulator allocation by (run_id, allocation_id): PENDING → IN_TRANSIT → ARRIVED / FAILED / CANCELLED, with timestamps (ticks)
- [ ] Update from `allocation.status_changed` SSE + REST refresh; persist transitions
- [ ] Expose a function Dev 4 can call: "what is the current status of allocation X / idempotency key K?\""""),
    (7, "[backend] Retry / stale / resync handling", ["backend", "P1"], """
State-level resilience (client-level retries are in the hardened client issue).

- [ ] Under `latency` / `error_rate` faults, dashboard keeps the last trusted state with its age, never labelled FRESH
- [ ] Stale or torn → schedule a full resync; recovery path: full REST fetch → reconcile allocations → fresh snapshot
- [ ] History gaps are marked `HISTORY_GAP`, never fabricated"""),
    (None, "[backend] Backend APIs for UI + intelligence", ["backend", "contract", "P1"], """
Endpoints the console and benchmark need beyond `/api/dashboard` (contract PR, `make contracts`):

- [ ] `GET /api/allocations` (tracked lifecycle), `GET /api/events`, `GET /api/demand-history?station_id&fuel_type&limit`
- [ ] `GET /api/decisions` (recommendation/decision history, with Dev 4)
- [ ] Keep `/api/dashboard` one fast poll; heavy history goes to its own endpoints"""),

    # ================================================================ DEV 2 — INTELLIGENCE
    (8, "[ai] Structural demand forecast", ["ai", "P0"], """
`app/intelligence/forecast.py` (baseline exists).

- [ ] profile × region factor × hour factor × live demand_multiplier, calibrated alpha on observed **demand_liters** (not served)
- [ ] Forecast error metric (e.g. MAPE over last N ticks) added to `Forecast` via a contract PR
- [ ] Tests on `normal` and `route-disruption` fixtures; deterministic"""),
    (None, "[ai] Crisis-aware forecast", ["ai", "P1"], """
Today alpha compares past observations against the *current* `demand_multiplier`, so it is skewed right after a spike starts.

- [ ] Use the multiplier in force at each observed tick (from `events`) when calibrating
- [ ] Anticipate SCHEDULED events (e.g. a demand_spike starting in 4 ticks) inside the horizon
- [ ] Apply `shipment_delay` / `supply_shortfall` effects to depot supply expectations
- [ ] Test: spike fixture where the naive forecast is measurably worse"""),
    (None, "[ai] Risk / time-to-stockout projector", ["ai", "P0"], """
The core signal of the demo. `app/intelligence/forecast.py` (projection) + `risk.py` (baseline exists).

- [ ] Projected inventory per tick = current − forecast demand + in-transit/pending arrivals (verified arrival semantics from Dev 1)
- [ ] Per station/fuel: time-to-safety-breach, time-to-stockout, earliest feasible arrival, inventory at arrival
- [ ] Levels: CRITICAL (stockout before any delivery can land), HIGH (below safety before arrival), WATCH, OK
- [ ] Reachability vs scarcity: `CONNECTIVITY_RISK` when no feasible route, separate from fuel shortage
- [ ] Route disruption changes the result (test on `route-disruption`)"""),
    (9, "[ai] Scarcity-aware allocation planner", ["ai", "P0"], """
`app/intelligence/planner.py` (arrival-aware order-up-to baseline exists).

- [ ] New fixture `tests/fixtures/scarcity.json`: two stations need the same fuel from one depot with insufficient stock/dispatch
- [ ] Allocation order: earliest breach first, lower redundancy second, then balance resulting cover (hours)
- [ ] Respect every simulator validation rule; depot reserve; min shipment; split above `max_shipment` when dispatch allows
- [ ] Deterministic (test: same input → same `Plan`)"""),
    (None, "[ai] Recommendation impact / confidence", ["ai", "contract", "P1"], """
- [ ] Impact: projected stockout avoided (ticks), liters of unmet demand avoided, inventory at arrival with vs without the shipment
- [ ] Confidence from **measured** forecast error only (e.g. margin vs recent error band). No uncalibrated probabilities like "87% chance of stockout"
- [ ] Fields added to `Recommendation` via a contract PR"""),
    (10, "[ai] Human-readable explanations", ["ai", "P1"], """
- [ ] Every recommendation: one-line summary, factors, stress margin, reason codes, alternatives with `rejected_because`
- [ ] Blocked cases explained (unreachable station, depot below reserve, dispatch full)
- [ ] Wording reviewed with Dev 3 so the panel reads well"""),
    (11, "[ai] Deterministic benchmark harness", ["ai", "P1"], """
`benchmark/`: a separate simulator instance, `/admin/reset` + `/admin/step`, apply `build_plan` each tick, record `/v1/metrics`.

- [ ] Same seed + same events → same numbers on re-run
- [ ] Compare do-nothing vs order-up-to vs scarcity planner; include one crisis (demand spike + route disruption)
- [ ] Markdown table: service level, unmet liters, allocation failures, stockout duration, shipments, planner runtime"""),

    # ================================================================ DEV 3 — FRONTEND
    (12, "[frontend] Operator console shell", ["frontend", "P0"], """
Grow `src/pages/OperationsDashboard.tsx` into `src/features/*`. Data: `GET /api/dashboard` (polled every 2 s).

- [ ] Header: tick, sim time, freshness, mode, tripwire, kill switch state; SIMULATION badge
- [ ] Grid layout from docs (network | priority risks / projection | current plan / supplies · events · decisions · health)
- [ ] Priority risks list from `plan.risks`
- [ ] Works in fake mode (`FIXTURE_SCENARIO=route-disruption`)"""),
    (15, "[frontend] Network + live inventory view", ["frontend", "P0"], """
- [ ] Depots → routes → stations (simple SVG/CSS, no graph library); DISRUPTED routes and OUTAGE stations highlighted
- [ ] Inventory per station/fuel as fill bars vs capacity and safety stock; depot inventory and dispatch capacity"""),
    (13, "[frontend] Projection / stockout-risk charts", ["frontend", "P0"], """
Recharts per station/fuel from `plan.projections` + `plan.risks`:
- [ ] Projected inventory line, safety-stock line, incoming shipment markers, recommended arrival marker
- [ ] Time-to-stockout / breach shown on the chart and in the risk list; select from the risk list"""),
    (None, "[frontend] Alerts + crisis/event view", ["frontend", "P1"], """
- [ ] Active / scheduled / resolved simulator events (`state.events`) with affected entities
- [ ] Alerts from tripwire WARNING trips (e.g. STATION_UNREACHABLE) and CRITICAL risks, newest first"""),
    (None, "[frontend] Recommendations + explanation panel", ["frontend", "P0"], """
- [ ] Each `Recommendation`: priority, quantity, depot → route → station, arrival tick, expiry tick, summary
- [ ] Expandable why: factors, reason codes, stress margin, alternatives with rejected reasons, impact/confidence when available"""),
    (16, "[frontend] Approval / execution controls", ["frontend", "P1"], """
- [ ] Approve / reject → `POST /api/recommendations/{id}/approve|reject`; show `RecommendationState`; handle 501/409 gracefully
- [ ] Mode selector + kill switch → `PUT /api/automation`; disabled when Tripwire TRIPPED
- [ ] Demo controls → `POST /api/sim/control` (run / pause / step)"""),
    (None, "[frontend] Decision / allocation history", ["frontend", "P1"], """
- [ ] Table of decisions (recommendation → approved → allocation id → status) and simulator allocations with progress
- [ ] Uses `recommendation_states` now; `GET /api/decisions` / `/api/allocations` when Dev 1 ships them"""),
    (14, "[frontend] System health + degraded-state UI", ["frontend", "P0"], """
- [ ] Banner when freshness is not FRESH/FIXTURE or `tripwire.state == TRIPPED`: "SIMULATOR DATA UNTRUSTED · last trusted tick N · age Xs · automatic execution paused"
- [ ] `state == null` renders without crashing (test with `FIXTURE_SCENARIO=does-not-exist`)
- [ ] Health panel from `health.components` (api, simulator, sse, snapshot, database, forecast, planner, tripwire, execution)
- [ ] Check with `FIXTURE_SCENARIO=stale`"""),

    # ================================================================ DEV 4 — RELIABILITY / DEVOPS
    (17, "[reliability] Tripwires (system + recommendation)", ["reliability", "P0"], """
`app/safety/tripwire.py`, deterministic, no LLM.

- [ ] System trips (block all execution): SNAPSHOT_STALE, SNAPSHOT_TORN, SIMULATOR_UNAVAILABLE, RESET_UNCERTAIN, RUN_ID_UNKNOWN, PERSISTENCE_FAILURE, EXECUTION_UNKNOWN
- [ ] Recommendation trips: RECOMMENDATION_EXPIRED → REPLAN; conditions changed since generation → REQUIRE_MANUAL_REVIEW
- [ ] Warnings: STATION_UNREACHABLE, FORECAST_DRIFT, PRIMARY_PLANNER_FAILED
- [ ] Unit test per rule"""),
    (18, "[reliability] Supervised execution gateway", ["reliability", "P0", "contract"], """
`app/decision/` + `/api/recommendations/{id}/approve|reject`.

- [ ] Lifecycle PROPOSED → APPROVED → EXECUTING → DONE / FAILED / EXPIRED / SUPERSEDED / REJECTED, persisted
- [ ] Before POST: fresh snapshot, Tripwire CLEAR, not expired, re-check route/station/depot/inventory/dispatch/destination capacity
- [ ] Outcome tracked through Dev 1's allocation lifecycle tracking"""),
    (None, "[reliability] Idempotency + execution locking", ["reliability", "P0"], """
- [ ] Persist intent + idempotency key (`{run_id}:{rec_id}:{chunk}`) as PREPARED before POST
- [ ] Ambiguous result (timeout after send) → retry with the SAME key and body; never a new key
- [ ] Per-depot lock: acquire → refresh → validate → POST → release; planning stays concurrent
- [ ] Test: two approvals racing on one depot do not both over-draw"""),
    (19, "[reliability] Modes + kill switch", ["reliability", "P1"], """
- [ ] ADVISORY / GUARDED_AUTO / MANUAL_DEMO persisted; GUARDED_AUTO refused while TRIPPED
- [ ] Guarded Auto executes PROPOSED recommendations only through the validated gateway
- [ ] Manual Demo: approve a whole plan; revalidated before each POST
- [ ] Kill switch overrides everything; every automatic action audited"""),
    (None, "[reliability] Graceful degradation / fallback", ["reliability", "P1"], """
- [ ] Planner exception → fallback plan + PRIMARY_PLANNER_FAILED, dashboard still renders
- [ ] Database unavailable → PERSISTENCE_FAILURE trip, advisory view still works, execution frozen
- [ ] Each component failure visible in `SystemHealth` with a detail message"""),
    (21, "[reliability] Fault-injection test suite", ["reliability", "P1"], """
`tests/scenarios/`: fake client that injects faults, plus a script that uses real `/admin/faults`.

- [ ] unavailable (503) → UNAVAILABLE + SIMULATOR_UNAVAILABLE trip, UI state null
- [ ] error_rate → retries succeed, no false FRESH; latency → still responsive
- [ ] stale_data → STALE + TRIPPED; torn read → TORN; stream_disconnect → SSE DEGRADED + resync
- [ ] Ambiguous POST → retry with same key does not duplicate"""),
    (None, "[reliability] Metrics / logs / health", ["reliability", "P1"], """
- [ ] Structured logs with run_id, tick, decision_id, allocation_id, component, error_code
- [ ] `/metrics` (Prometheus): snapshot age, tick lag, stale count, planner runtime, recommendations, allocation attempts/conflicts/failures, service level
- [ ] Optional Grafana dashboard in `infra/grafana`"""),
    (None, "[reliability] Load test", ["reliability", "P1"], """
`infra/k6/`: load `/api/dashboard` and `/api/health` with the backend in real mode.
- [ ] Record p50/p95 latency and error rate at e.g. 20 virtual users; note settings in the result"""),
    (20, "[reliability] Docker Compose", ["reliability", "P0"], """
- [ ] `git clone && cp .env.example .env && docker compose up --build` works; frontend reaches backend
- [ ] SQLite survives `docker compose restart backend` (`fuel_data` volume)
- [ ] `make up-sim` + `SIMULATOR_MODE=real` works against the published simulator image"""),
    (None, "[reliability] CI", ["reliability", "P1"], """
- [ ] CI green on `staging` (ruff, pytest, OpenAPI drift, TS types drift, oxlint, build)
- [ ] Protect `main` and `staging`: PR + green CI required
- [ ] Add fault scenario tests to CI once they exist"""),
    (None, "[reliability] Demo / recovery scenario automation", ["reliability", "P0"], """
Script the demo so it is repeatable (`benchmark/scenarios/` or `scripts/demo.py`):

- [ ] Reset → run → inject demand_spike + route_disruption → recommendations reroute
- [ ] Inject stale_data / unavailable fault → Tripwire TRIPPED → clear → full resync → automation resumes
- [ ] Approve one recommendation → allocation visible in simulator → ARRIVED
- [ ] Backup screen recording"""),
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
    for number, title, labels, body in ISSUES:
        full_body = body.strip() + COMMON
        if number is not None:
            drop = sorted(PRIORITIES - set(labels))
            args = ["issue", "edit", str(number), "--repo", REPO, "--title", title, "--body", full_body,
                    "--add-label", ",".join(labels), "--milestone", MILESTONE]
            if drop:
                args += ["--remove-label", ",".join(drop)]
            gh(*args)
            print(f"edit   #{number} {title}")
        elif title in existing:
            print(f"skip   {title}")
        else:
            url = gh("issue", "create", "--repo", REPO, "--title", title, "--body", full_body,
                     "--label", ",".join(labels), "--milestone", MILESTONE).strip()
            print(f"create {url}")


if __name__ == "__main__":
    main()
