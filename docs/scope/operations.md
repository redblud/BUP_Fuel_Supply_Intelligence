# Operations

## Three hour gate

### 9. Live operator dashboard · planned
Show the current network, fuel inventory, freshness, risk, projection, and recommendation in one screen. Read only the backend API.
**Done when:** a live scenario shows current tick and inventory plus one explained Advisory recommendation, and unavailable data is visibly degraded.
- [ ] Build the live operator dashboard: `/develop live operator dashboard`

## After the three hour gate

### 10. Expanded operator dashboard · planned · needs a decision
Grow the first screen to show incoming shipments and depot supplies, active events, network topology, projection charts, stress margins, alternative actions, decision history, and component health.
**Done when:** an operator can inspect why a recommendation exists, what else was considered, what is arriving, and whether the system is safe to act.
- [ ] Design the expanded operator view (spec): `/architect expanded operator dashboard`

### 11. Tripwire and Safety Guard · planned · needs a decision
Evaluate whether trusted state, system health, execution health, and audit persistence permit a proposed action. Keep this deterministic, explainable guard between the pure planner and the Decision Orchestrator, with no LLM decision path. Each trip carries a code, severity, message, affected scope, detected tick, and required actions.
**Done when:** CLEAR or TRIPPED and active trip details are visible in the API and dashboard; stale or torn snapshots, simulator loss, uncertain reset or execution, unavailable audit persistence, or unknown run identity freeze Guarded Auto; expired recommendations trigger scoped replanning; and the manual kill switch always overrides execution.
- [ ] Design Tripwire rules and responses (spec): `/architect tripwire and safety guard`

### 12. Recommendation lifecycle and supervised execution · planned · needs a decision
Track plans from proposal through outcome, and let an operator approve a plan only after fresh validation. Coordinate execution, same depot serialization, idempotency, and ambiguous response recovery outside the pure planner.
**Done when:** approvals are authorized and audited, stale proposals expire, pre-execution validation blocks changed conditions, duplicate submissions are prevented, and submitted allocations are observed to completion or failure.
- [ ] Design recommendation lifecycle and supervised execution (spec): `/architect recommendation lifecycle and supervised execution`

### 13. Operating modes and guarded automation · planned · needs a decision
Support Advisory, Guarded Auto, and Manual Demo modes. Permit routine replenishment only when the Tripwire is clear and execution policy allows it. Show mode, automation status, and a kill switch.
**Done when:** the Decision Orchestrator cannot bypass a critical Tripwire, Manual Demo revalidates approved batch plans, and every automatic action remains visible and auditable.
- [ ] Design operating modes and guarded automation (spec): `/architect operating modes and guarded automation`

### 14. Recovery, health, and security · planned · needs a decision
Handle simulator faults, event stream disconnects, resets, startup recovery, and degraded operation. Expose component health, logs, metrics, and drift signals; protect consequential controls.
**Done when:** faults cannot create silent unsafe execution, recovery reconciles state before resuming, health identifies the failing component, useful performance and decision metrics are visible, and control access is restricted.
- [ ] Design recovery, health, and security (spec): `/architect recovery health and security`
