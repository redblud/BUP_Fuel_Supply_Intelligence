# FuelOps AI

Operational decision-support system for the BUP Fuel Supply Simulator: observe the network,
build a trusted snapshot, forecast shortages, recommend explained replenishment, check it is
safe to act, execute, and recover from faults.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000/api/health
- FastAPI docs: http://localhost:8000/docs

This runs against the **fake simulator** (`tests/fixtures/route-disruption.json`), so no simulator is needed.
To use the real BUP simulator, set `SIMULATOR_MODE=real` in `.env` and run `make up-sim`
(it is published on host port 9000; the backend reaches it at `http://simulator:8000`).

### Without Docker

```bash
make backend    # needs uv: https://docs.astral.sh/uv/
make frontend   # needs Node 22+; run `npm ci` in apps/web first
```

## Repository

| Path | Owner | What |
|---|---|---|
| `apps/api/app/simulator/` | Dev 1 | Only code that talks to the simulator. `RealSimulatorClient`, `FakeSimulatorClient`, error normalization, mapper |
| `apps/api/app/state/` | Dev 1 | State Reconciler → `NetworkState` with freshness (FRESH / STALE / TORN / …) |
| `apps/api/app/intelligence/` | Dev 2 | Pure forecast, topology, risk, planner. No HTTP, DB, or clock |
| `apps/web/` | Dev 3 | React operator console. Reads only `/api/*` |
| `apps/api/app/safety/`, `decision/`, CI, `infra/` | Dev 4 | Tripwire, orchestrator, execution, reliability |
| `apps/api/app/domain/models.py` | **Everyone** | Frozen shared contracts. Change via a contract PR |
| `tests/fixtures/` | Everyone | Fake-simulator scenarios |

## Common commands

```bash
make check       # lint + tests + frontend build (what CI runs)
make contracts   # after changing domain models or routes: regenerate OpenAPI + TS types
make fixtures    # regenerate tests/fixtures scenarios
```

## Docs

- [Architecture](docs/architecture.md)
- [API contracts](docs/api-contracts.md)
- [Simulator semantics](docs/simulator-semantics.md)
- [Development workflow](docs/development.md)
- [Project plan](docs/project-plan.md)
- [Deliverables](docs/deliverables.md)

## Current status

Foundation: the full vertical slice runs on fixtures. The fake simulator feeds the reconciler, which builds a `NetworkState`.
The baseline planner turns it into a `Plan`, the Tripwire checks it, and the dashboard shows the result. SQLite (WAL) persists on a volume.
Work is tracked in GitHub issues by track: `backend`, `ai`, `frontend`, `reliability`.
