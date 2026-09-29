# Scope: FuelOps AI

FuelOps AI is a simulated fuel distribution decision system for an operator. The full brief is represented below, including delivery evidence and later intelligence. Your team is building in parallel across frontend, backend, and AI work.

**Build approach:** Tracer Bullet (connect the real simulator, decision rule, and operator screen early).
**Workflow:** Alpha (check each built feature in the running app). A feature with a load bearing decision starts with `/architect`.

**Three hour gate:** A live Advisory dashboard shows current simulator state, freshness, one arrival aware risk, and an explained replenishment recommendation. Backend owns simulator access and the canonical snapshot. AI owns pure forecast, risk, and planning functions against a saved snapshot. Frontend owns the dashboard against the same response contract. Agree on the contract and fixture in the first 20 minutes, build in parallel for about 100 minutes, then reserve the final hour for integration, a live scenario, and fixes.

**Feasibility:** The complete brief cannot credibly be implemented and verified by the team in three hours. The Tripwire, supervised execution, guarded automation, full recovery, load and benchmark evidence, and advanced intelligence remain in this full scope, but they are beyond the three hour gate. A recorded fixture keeps work moving if the simulator is unavailable; it does not satisfy the live gate.

_These are recommended steps. You can skip a planning command when its decision is already settled. The first unchecked box in each feature is the next suggested action._

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| 1 | [Stack and architecture](foundation.md#1-stack-and-architecture) | Foundation | in-progress |
| 2 | [Coding standards and tooling](foundation.md#2-coding-standards-and-tooling) | Foundation | planned |
| 3 | [Simulator characterization and adapter](foundation.md#3-simulator-characterization-and-adapter) | Foundation | planned |
| 4 | [Canonical state and persistence](foundation.md#4-canonical-state-and-persistence) | Foundation | planned |
| 5 | [Dashboard UI foundation](foundation.md#5-dashboard-ui-foundation) | Foundation | planned |
| 6 | [Demand forecast and inventory projection](intelligence.md#6-demand-forecast-and-inventory-projection) | Three hour gate | planned |
| 7 | [Arrival aware risk and topology](intelligence.md#7-arrival-aware-risk-and-topology) | Three hour gate | planned |
| 8 | [Pure replenishment planner and explanations](intelligence.md#8-pure-replenishment-planner-and-explanations) | Three hour gate | planned |
| 9 | [Live operator dashboard](operations.md#9-live-operator-dashboard) | Three hour gate | planned |
| 10 | [Expanded operator dashboard](operations.md#10-expanded-operator-dashboard) | After gate | planned |
| 11 | [Tripwire and Safety Guard](operations.md#11-tripwire-and-safety-guard) | After gate | planned |
| 12 | [Recommendation lifecycle and supervised execution](operations.md#12-recommendation-lifecycle-and-supervised-execution) | After gate | planned |
| 13 | [Operating modes and guarded automation](operations.md#13-operating-modes-and-guarded-automation) | After gate | planned |
| 14 | [Recovery, health, and security](operations.md#14-recovery-health-and-security) | After gate | planned |
| 15 | [Packaging and automated verification](delivery.md#15-packaging-and-automated-verification) | After gate | planned |
| 16 | [Benchmark, load, and submission evidence](delivery.md#16-benchmark-load-and-submission-evidence) | After gate | planned |
| 17 | [Evidence gated advanced intelligence](delivery.md#17-evidence-gated-advanced-intelligence) | Evidence gated | planned |

## Areas

- [Foundations](foundation.md): application boundaries, simulator access, trusted state, and UI base. Five planned.
- [Intelligence](intelligence.md): forecast, risk, and pure planning. Three planned.
- [Operations](operations.md): dashboard, Tripwire, execution, automation, and recovery. Six planned.
- [Delivery](delivery.md): runnable package, verification, evidence, and optional advanced methods. Three planned.

## Legend

**Planned** means no implementation has been verified yet. **Needs a decision** means the listed `/architect` step should settle behavior before building. The three hour gate is a checkpoint, not a claim that the entire brief fits the deadline. The brief remains the detailed source for later specs.
