# Intelligence

## Three hour gate

### 6. Demand forecast and inventory projection · planned · needs a decision
Estimate near term demand and project station inventory through the earliest useful delivery time. Start with structural demand, then calibrate from history and expose assumptions.
**Done when:** a saved snapshot produces repeatable demand and inventory curves with correct tick and hour alignment, incoming fuel, allocation lifecycle, and measured forecast error.
- [ ] Design forecast and projection behavior (spec): `/architect demand forecast and inventory projection`

### 7. Arrival aware risk and topology · planned · needs a decision
Find feasible live routes and identify where projected inventory crosses demand based safety stock before replenishment can arrive. Distinguish unreachable stations from scarce fuel.
**Done when:** a repeatable case ranks station and fuel risk using feasible arrival time and safety margin, and route disruption changes the result.
- [ ] Design risk and topology rules (spec): `/architect arrival aware risk and topology`

### 8. Pure replenishment planner and explanations · planned · needs a decision
Recommend a feasible source, amount, and timing without simulator calls or database access. Account for competing demand, depot reserve, shipment limits, route ranking, scarcity priority, and alternatives.
**Done when:** the same snapshot and policy yield the same plan without a running server; the plan explains stress margin, blocked cases, and alternatives; and a deterministic fallback remains available if the primary planner fails.
- [ ] Design the pure planning rule (spec): `/architect pure replenishment planner and explanations`
