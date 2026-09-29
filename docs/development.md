# Development

## Branches

```
main      ← protected, tagged releases (foundation-v1, ...)
staging   ← integration branch; all feature PRs target this
feat/simulator-state         Dev 1
feat/intelligence-baseline   Dev 2
feat/operator-dashboard      Dev 3
feat/execution-reliability   Dev 4
```

```bash
git fetch origin
git switch -c feat/<name> origin/staging
```

Keep PRs small and merge into `staging` often (at least hourly during the hackathon). Rebase on `staging` before
opening a PR. Merge `staging` into `main` at integration checkpoints.

## Ownership (avoid merge conflicts)

| You are | You edit | Ask before touching |
|---|---|---|
| Dev 1 | `app/simulator/`, `app/state/`, `app/persistence/` (observations), `tests/fixtures/` recordings | `app/domain/models.py` |
| Dev 2 | `app/intelligence/`, `tests/test_intelligence.py`, `benchmark/` | `app/domain/models.py` |
| Dev 3 | `apps/web/` (except `src/api/generated/`) | anything under `apps/api` |
| Dev 4 | `app/safety/`, `app/decision/`, `app/api/routes/`, `.github/`, `infra/`, `docker-compose.yml` | `app/domain/models.py` |

`app/domain/models.py` is shared: change it only in a contract PR (see `api-contracts.md`).

## Everyday loop

```bash
make backend           # API on :8000 with reload (fake simulator by default)
make frontend          # UI on :5173, proxies /api to :8000
make check             # before every PR
make contracts         # after any model/route change; commit the generated files
```

Switch scenario: `FIXTURE_SCENARIO=stale make backend`. Real simulator: run it (`make up-sim`, or its own compose on
:8000). Then set `SIMULATOR_MODE=real` and `SIMULATOR_BASE_URL=http://127.0.0.1:9000` (or `:8000`), and start the backend on a free port.

## Definition of done for a PR

- `make check` passes; CI is green.
- New behavior has a test that runs without the real simulator.
- Any simulator assumption is recorded in `simulator-semantics.md`.
