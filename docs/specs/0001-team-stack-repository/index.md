# 0001. Team stack and repository foundation

**Date**: 2026-09-29  
**Status**: In Progress

## Summary

Use one repository and one Python API process for the first FuelOps AI demo. Keep the simulator adapter, decision functions, and React dashboard in separate folders with one generated API contract. Docker Compose starts the local system so three teammates can build and integrate through short pull requests.

## Decision

**Chosen option**: A modular Python application with a separate browser client in one repository. (basis: `docs/scope/index.md`, the three hour gate; [FastAPI features](https://fastapi.tiangolo.com/features/))

The API owns simulator access, the trusted snapshot, persistence, and public responses. The intelligence package accepts saved state and policy as inputs and returns repeatable forecast, risk, and plan results. The browser reads only the API. No browser or intelligence code calls the simulator directly.

## Proposed stack

| Layer | Choice | Reason |
|---|---|---|
| Architecture | One API process with internal modules | A three person team can debug one request and one state path. |
| Backend | Python with FastAPI and Pydantic models | Typed validation produces an OpenAPI description for the frontend. |
| Intelligence | Pure Python package called by the API | Saved inputs produce repeatable results without a second service. |
| Frontend | React, TypeScript, and Vite | The operator screen starts quickly and consumes typed API responses. |
| Database | PostgreSQL in Docker Compose | Durable snapshots and later audit records share one relational store. |
| Data access | SQLAlchemy with Alembic migrations | The API owns queries and explicit schema changes. |
| API contract | FastAPI OpenAPI schema and generated TypeScript types | One schema governs backend validation and frontend compile checks. |
| Browser updates | Poll one dashboard response | Each read can include state, freshness, risk, and recommendation together. |
| Simulator updates | API polls the provided HTTP service | Only the adapter needs its URL and eventual API details. |
| Local runtime | Docker Compose | One command starts the API, web client, and database on a local machine. |
| Dependencies | `uv.lock` for Python and `package-lock.json` for npm | Teammates and CI install the same versions. |
| Access for the first gate | Local Advisory view without sign in | No action endpoint or automated execution is part of the first gate. |
| Checks | GitHub Actions on pull requests | Lint, type checks, focused tests, contract generation check, and builds run without a live simulator. |
| Observability | Structured API logs and separate liveness and readiness checks | A failed simulator read or database connection is visible during integration. |

## Repository and integration contract

| Path | Owner | Boundary |
|---|---|---|
| `apps/api/` | Backend | FastAPI entry point, simulator adapter, snapshot orchestration, persistence, public API, and migrations. |
| `packages/intelligence/` | AI | Forecast, arrival aware risk, and planning functions with no HTTP, database, or FastAPI imports. |
| `apps/web/` | Frontend | React screen, generated API types, and API client. It makes no simulator calls. |
| `contracts/fixtures/` | Shared | Recorded simulator responses and one representative dashboard response for development and checks. Fixtures are versioned with the contract. |
| `compose.yaml` | Shared | Local service wiring, health checks, and persistent PostgreSQL volume. |
| `docs/` | Shared | Scope, decisions, simulator findings, and demo instructions. |

The API exposes a versioned dashboard read under `/api/v1` and separate liveness and readiness reads. Exact response fields, freshness rules, and simulator mapping belong to the canonical state, intelligence, and dashboard specs. Generate frontend types from the API schema. A contract change and its representative fixture land in the same pull request, with review from the affected tracks. The fixture allows independent work but does not count as a live simulator demonstration. (basis: `docs/scope/index.md`, the contract and fixture gate; [OpenAPI TypeScript project](https://github.com/openapi-ts/openapi-typescript))

Use a root Python workspace with `apps/api/` depending on `packages/intelligence/`. Keep npm in `apps/web/`. The API depends on intelligence, while intelligence has no dependency on the API. The frontend compiles against generated API types rather than a second handwritten schema. (basis: [uv project guide](https://docs.astral.sh/uv/guides/projects/), [Vite guide](https://vite.dev/guide/))

The first integration slice is a live simulator read through the API, one saved snapshot, one deterministic risk and recommendation, and one browser display. Freeze the first dashboard response and fixture in the first 20 minutes. Integrate short branches into `main` through reviewed pull requests throughout the gate, then reserve the final hour for a live scenario and fixes. Run locked installs, lint, type checks, focused tests, and builds in GitHub Actions for each pull request. (basis: `docs/scope/index.md`, the Tracer Bullet approach and timing; [GitHub Actions Python guide](https://docs.github.com/en/actions/tutorials/build-and-test-code/python))

The API reads simulator state on a configurable interval. It records the last successful observation and exposes freshness in the dashboard response. On failed reads, it marks the response degraded and never labels an old snapshot as live. The canonical state spec sets the exact timing and reconciliation rule after the simulator is characterized. The database remains internal to Compose. The API and web ports bind to the local machine for this demo. (basis: `docs/scope/foundation.md`, canonical state intent)

## Consequences

**Positive**:

- One contract and fixture let backend, AI, and frontend work at the same time.
- A pure intelligence package can be checked against saved cases without a running server.
- PostgreSQL and migrations leave room for later audit and execution work.

**Negative and tradeoffs**:

- Compose and PostgreSQL add setup cost before the first screen appears.
- Generated types require a repeatable generation step and a contract check in CI.
- Polling adds periodic API and simulator reads; the interval must follow observed simulator behavior.

## Follow-up

- [ ] Obtain the simulator base URL, API details, and representative responses before implementing the adapter.
- [ ] Set the poll interval and freshness threshold from simulator timing evidence in the canonical state spec.
- [ ] Define the dashboard response fields and example fixture before the three tracks branch.
- [ ] Add root project guidance and exact commands after the scaffold exists, through `/audit` or `/sync`.

## Rationale

Reasoning, alternatives, and verified sources: see [rationale.md](rationale.md).
