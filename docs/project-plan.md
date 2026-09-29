# Project plan (2 hour build)

| Time | Everyone |
|---|---|
| 0:00–0:10 | Clone `staging`, `cp .env.example .env`, `make up` (or `make backend` + `make frontend`). Read `api-contracts.md`. Pick up your issues. |
| 0:10–1:20 | Build in parallel on your branch against fixtures. PR into `staging` at least every 30 min. |
| 1:20–1:40 | Integration: real simulator, `SIMULATOR_MODE=real`, run the demo scenario end to end, fix. |
| 1:40–2:00 | Freeze. Demo rehearsal, README/docs pass, merge `staging` → `main`, tag. |

## Tracks and GitHub labels

| Dev | Track | Label | Milestone focus |
|---|---|---|---|
| 1 | Simulator + State | `backend` | Real simulator read → trusted `NetworkState`; SSE hint; reset detection; persisted demand history |
| 2 | Intelligence | `ai` | Calibrated forecast, scarcity-aware planner, explanations, forecast-error metric |
| 3 | Frontend | `frontend` | Operator console: network, risks, projection chart, recommendations, events, health, controls |
| 4 | Safety + Decision + Reliability | `reliability` | Tripwire rules, approval + idempotent execution, modes + kill switch, CI/Docker, fault scenario tests |

## Demo gate (must work live)

A live Advisory dashboard shows the current simulator tick, freshness, at least one arrival-aware risk, and an explained
recommendation. Then:
1. Inject `route_disruption` → the plan reroutes and the dashboard shows the alternative.
2. Inject a `stale_data` fault → Tripwire TRIPPED, automation paused, last trusted tick shown.
3. Approve one recommendation → the allocation appears in the simulator and arrives.
