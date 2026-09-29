# Team stack and repository foundation: rationale

## Context

FuelOps AI has three parallel work tracks and a three hour live Advisory checkpoint. The scope calls for one backend between the simulator and operator screen, a canonical saved snapshot, and pure decision functions. The simulator is an external HTTP service, but its URL and API details are not in this repository yet. A recorded fixture can keep teammates moving but cannot prove the live checkpoint. (basis: `docs/scope/index.md`, `docs/scope/foundation.md`)

There is no application scaffold or root `AGENTS.md` yet. The first foundation item needs a shared stack and module boundary before the three tracks can start. The team chose a local Docker Compose demo, Python for backend and intelligence, TypeScript for the browser, and reviewed pull requests. (basis: repository inventory on 2026-09-29, design conversation)

## Options considered

### Option 1: One modular API and one browser client in one repository

FastAPI owns state and responses, the intelligence package is called in process, and React reads a versioned API. One pull request can update schema, fixture, and client types together. The cost is coordinating changes to shared contracts and running PostgreSQL locally. (basis: `docs/scope/index.md`; [FastAPI features](https://fastapi.tiangolo.com/features/))

### Option 2: Separate services or repositories for each track

Each team could deploy or version its part independently. The cost is network contracts, cross repository changes, and more startup and failure paths during a short integration window. (basis: `docs/scope/index.md`, the three hour gate; service boundary practice)

### Option 3: Single browser application with direct simulator access

This would remove the API process from the first screen. It would also put simulator details and decision authority in the client, so canonical state and later guarded actions would need a redesign. (basis: `docs/scope/foundation.md`, backend ownership of simulator access)

## Rationale

Option 1 matches the recorded split of responsibility and the team size. It gives each track a folder it can own while preserving one place to normalize simulator state and enforce the public contract. FastAPI OpenAPI output can drive TypeScript types, which makes contract drift visible at compile time. A PostgreSQL service costs more setup than SQLite but fits durable snapshots and the later audit path already in scope. (basis: `docs/scope/index.md`; [FastAPI features](https://fastapi.tiangolo.com/features/), [OpenAPI TypeScript project](https://github.com/openapi-ts/openapi-typescript), [PostgreSQL overview](https://www.postgresql.org/docs/current/intro-whatis.html), [SQLite usage guidance](https://www.sqlite.org/whentouse.html))

The first gate is a local Advisory view, so sign in and execution controls would delay the live path. The application must stay on the local machine for that gate. Consequential controls need their own authorization decision before they are exposed. Polling is the simplest first read path, with degraded state visible when the simulator fails; exact timing waits for simulator characterization. (basis: `docs/scope/operations.md`, `docs/scope/foundation.md`)

## References

**Project sources**:

- `docs/scope/index.md`: three hour gate, track ownership, and Tracer Bullet approach.
- `docs/scope/foundation.md`: backend boundary and canonical state intent.
- `docs/scope/operations.md`: Advisory view and later protected controls.

**Practices and standards**:

- Modular application boundaries for a small team.
- Versioned API schema with generated client types.
- Short reviewed pull requests with checks that do not require the external simulator.

**Links**:

- [FastAPI features](https://fastapi.tiangolo.com/features/)
- [Vite guide](https://vite.dev/guide/)
- [OpenAPI TypeScript project](https://github.com/openapi-ts/openapi-typescript)
- [PostgreSQL overview](https://www.postgresql.org/docs/current/intro-whatis.html)
- [SQLite usage guidance](https://www.sqlite.org/whentouse.html)
- [uv project guide](https://docs.astral.sh/uv/guides/projects/)
- [GitHub Actions Python guide](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
