# Delivery and evidence

## After the three hour gate

### 15. Packaging and automated verification · planned · needs a decision
Make the system repeatable to start and safe for teammates to change. Cover environment configuration, persistent database and migrations, containers, repository workflow, domain and fault tests, and continuous checks.
**Done when:** a clean checkout starts with documented commands, data survives a backend restart, and local and continuous checks pass without a live simulator.
- [ ] Design packaging and verification (spec): `/architect packaging and automated verification`

### 16. Benchmark, load, and submission evidence · planned · needs a decision
Measure decision quality and application performance in isolated runs with demo and test controls. Package the architecture, API contracts, simulator semantics, assumptions, development guide, README, demo script, and backup recording.
**Done when:** benchmark and load results have recorded settings and metrics, documented claims match implemented behavior, and the demo can be repeated from the written instructions.
- [ ] Design the evidence plan (spec): `/architect benchmark load and submission evidence`

## Evidence gated

### 17. Evidence gated advanced intelligence · planned · needs a decision
Consider scarcity optimization, learned forecasting, anomaly detection, scheduled event anticipation, and generative explanations only when a measured baseline shows a useful gap.
**Done when:** any added method improves a defined benchmark and leaves the fallback rule and operator explanation reliable.
- [ ] Decide which advanced method earns a build (spec): `/architect evidence gated advanced intelligence`
