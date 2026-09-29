# Foundations

## Foundation

### 1. Stack and architecture · in-progress
Keep a modular application with one backend between the simulator and frontend. Start a runnable skeleton with a clear contract for the three work tracks.
**Done when:** backend and frontend start locally, a health response works, and module boundaries are recorded.
**Spec:** [0001 Team stack and repository foundation](../specs/0001-team-stack-repository/index.md)
- [x] Decide the stack and architecture (spec): `/architect stack and architecture`
- [x] Scaffold from the decision: `/develop stack and architecture` (code: `apps/api/`, `apps/web/`, `packages/intelligence/`, `compose.yaml`; see `docs/architecture.md`)
- [ ] Verify it: `/check verify stack and architecture`

### 2. Coding standards and tooling · planned
Capture the minimum conventions and ownership boundaries that let three people work without conflicting changes.
**Done when:** root guidance reflects the actual scaffold and agreed checks run locally.
- [ ] Capture conventions and tooling choices: `/audit`

### 3. Simulator characterization and adapter · planned · needs a decision
Confirm the simulator's state, timing, route, supply, cancellation, and allocation semantics, then isolate raw requests and errors behind one adapter. Keep recorded response examples for independent development and separate demo controls from normal operations.
**Done when:** current simulator state is fetched through the backend, the specified characterization questions are classified as documented, verified, or unverified, and failures are normalized.
- [ ] Design the adapter and characterization plan (spec): `/architect simulator characterization and adapter`

### 4. Canonical state and persistence · planned · needs a decision
Create a normalized snapshot with run identity, tick, freshness, inventories, routes, demand, arrivals, and events. Use event notifications to trigger authoritative state reads and retain demand and allocation history.
**Done when:** consumers use one stable snapshot, stale or torn state is not presented as trusted, event disconnect and reset trigger reconciliation, and needed observations survive restart.
- [ ] Design canonical state and persistence (spec): `/architect canonical state and persistence`

### 5. Dashboard UI foundation · planned · needs a decision
Define the single operator screen's layout, status language, and readable visual treatment for inventory, risk, and explanations.
**Done when:** the base screen works on a laptop and clearly distinguishes simulation, live, stale, and unavailable state.
- [ ] Design the dashboard UI foundation (spec): `/architect dashboard UI foundation`
