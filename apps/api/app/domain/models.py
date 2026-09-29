"""Shared contract types: NetworkState, Forecast, RiskAssessment, Recommendation, Plan.

Single source of truth. The API embeds these in its responses, so a field change here
changes `contracts/openapi.json` and the generated frontend types. Change it in a
contract PR with `contracts/fixtures` regenerated in the same PR.

Simulator entity shapes mirror the simulator's `/v1/*` responses field for field.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FuelType = Literal["DIESEL", "PETROL", "OCTANE"]
FUEL_TYPES: tuple[FuelType, ...] = ("DIESEL", "PETROL", "OCTANE")

FuelLiters = dict[FuelType, float]


# ---------------------------------------------------------------------------
# Simulator entities (mirror /v1/* on the simulator)
# ---------------------------------------------------------------------------


class RunInfo(BaseModel):
    """Simulator `/v1/instance`."""

    scenario_id: str
    scenario_version: str
    seed: int
    tick: int
    tick_minutes: int
    sim_time: datetime
    status: Literal["PAUSED", "RUNNING"]


class Region(BaseModel):
    id: str
    name: str
    demand_factor: float


class Depot(BaseModel):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "CONSTRAINED"]
    dispatch_capacity_per_tick: float
    capacity: FuelLiters
    inventory: FuelLiters


class Station(BaseModel):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "OUTAGE"]
    demand_profile: str
    demand_multiplier: float
    capacity: FuelLiters
    inventory: FuelLiters


class Route(BaseModel):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int
    max_shipment: float
    status: Literal["AVAILABLE", "DISRUPTED"]


class SupplyArrival(BaseModel):
    id: str
    depot_id: str
    fuel_type: FuelType
    quantity: float
    planned_tick: int
    actual_tick: int | None
    status: Literal["SCHEDULED", "DELAYED", "ARRIVED"]


class SimEvent(BaseModel):
    id: int
    type: Literal[
        "demand_spike",
        "route_disruption",
        "station_outage",
        "depot_constraint",
        "shipment_delay",
        "supply_shortfall",
    ]
    start_tick: int
    end_tick: int
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"]
    parameters: dict[str, Any] = Field(default_factory=dict)


AllocationStatus = Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"]


class Allocation(BaseModel):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float
    created_tick: int
    departure_tick: int | None
    expected_arrival_tick: int | None
    actual_arrival_tick: int | None
    status: AllocationStatus
    failure_reason: str | None


class DemandObservation(BaseModel):
    station_id: str
    fuel_type: FuelType
    tick: int
    demand_liters: float
    served_liters: float
    unmet_liters: float


class HistoryGap(BaseModel):
    """Demand ticks we never observed for one station/fuel (`HISTORY_GAP`). Never filled with invented values."""

    station_id: str
    fuel_type: FuelType
    from_tick: int
    to_tick: int


class SimMetrics(BaseModel):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


class AllocationTransition(BaseModel):
    """One observed status of a simulator allocation, with the simulator tick it applies to."""

    run_id: str
    allocation_id: int
    status: AllocationStatus
    tick: int
    observed_at: datetime


class TrackedAllocation(BaseModel):
    """A simulator allocation and its observed lifecycle. Identity is (run_id, allocation_id)."""

    run_id: str
    allocation: Allocation
    transitions: list[AllocationTransition]


# ---------------------------------------------------------------------------
# NetworkState (the trusted snapshot)
# ---------------------------------------------------------------------------

SnapshotFreshness = Literal["FRESH", "STALE", "TORN", "UNAVAILABLE", "RESET_UNCERTAIN", "DEGRADED", "FIXTURE"]


class SnapshotMeta(BaseModel):
    """How far to trust this state. Set by the State Reconciler, read by Tripwire and UI."""

    run_id: str = Field(description="Logical simulator run; changes on reset. Identity is (run_id, allocation_id).")
    tick_start: int = Field(description="Instance tick read before the parallel entity reads.")
    tick_end: int = Field(description="Instance tick read after the parallel entity reads.")
    retrieved_at: datetime
    latest_sse_tick: int | None = None
    stale: bool = Field(description="Simulator sent X-Simulator-Stale: true on any read.")
    consistent: bool = Field(description="tick_end - tick_start within tolerance.")
    freshness: SnapshotFreshness


class NetworkState(BaseModel):
    """One consistent view of the world. Built only by the API; intelligence never fetches.

    `demand_history` holds recent observations, oldest first: the simulator window merged with what we persisted,
    so it can be longer than the 200-row REST window. `history_gaps` lists observation ticks that are missing.
    """

    meta: SnapshotMeta
    run: RunInfo
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    supply_arrivals: list[SupplyArrival]
    events: list[SimEvent]
    allocations: list[Allocation]
    demand_history: list[DemandObservation]
    history_gaps: list[HistoryGap] = Field(default_factory=list)
    metrics: SimMetrics | None = None


# ---------------------------------------------------------------------------
# Intelligence inputs and outputs
# ---------------------------------------------------------------------------


class Policy(BaseModel):
    """Tunable planner knobs. Defaults are the P0 arrival-aware order-up-to policy."""

    horizon_ticks: int = 32
    safety_stock_hours: float = 4.0
    target_cover_hours: float = 12.0
    target_fill_fraction: float = 0.9
    depot_reserve_fraction: float = 0.1
    min_shipment_liters: float = 500.0
    recommendation_ttl_ticks: int = 4
    history_calibration_ticks: int = 16
    forecast_error_ticks: int = Field(default=8, description="Most recent observed ticks scored for forecast error.")


class Forecast(BaseModel):
    """Forecast of demand_liters (not served_liters) per tick."""

    station_id: str
    fuel_type: FuelType
    start_tick: int
    liters_per_tick: list[float]
    calibration: float = Field(description="alpha in forecast = alpha x structural demand.")
    method: str
    error_mape: float | None = Field(
        default=None,
        description="Mean absolute percentage error of one-step-ahead backtest forecasts vs observed demand_liters; null if nothing scoreable.",
    )
    error_ticks: int = Field(default=0, description="Observed ticks scored in error_mape.")


class ProjectionPoint(BaseModel):
    tick: int
    inventory: float
    incoming: float = 0.0


class InventoryProjection(BaseModel):
    station_id: str
    fuel_type: FuelType
    safety_stock: float
    points: list[ProjectionPoint]


RiskLevel = Literal["OK", "WATCH", "HIGH", "CRITICAL"]
ReasonCode = Literal[
    "SAFETY_STOCK_BREACH",
    "STOCKOUT_BEFORE_ARRIVAL",
    "CONNECTIVITY_RISK",
    "SINGLE_SOURCE",
    "STATION_OUTAGE",
    "SCARCITY_LIMITED",
    "ROUTE_DISRUPTED",
]


class RiskAssessment(BaseModel):
    station_id: str
    fuel_type: FuelType
    level: RiskLevel
    reason_codes: list[ReasonCode]
    current_inventory: float
    safety_stock: float
    projected_inventory_at_arrival: float | None
    projected_safety_breach_tick: int | None
    projected_stockout_tick: int | None
    hours_to_safety_breach: float | None = Field(
        default=None, description="Simulated hours from now to projected_safety_breach_tick; null if none in the horizon."
    )
    hours_to_stockout: float | None = Field(
        default=None, description="Simulated hours from now to projected_stockout_tick; null if none in the horizon."
    )
    earliest_arrival_tick: int | None = Field(description="Earliest tick a new shipment could land; null if unreachable or no depot has stock.")
    redundancy: int = Field(description="Number of currently feasible routes to this station for this fuel.")
    reason: str


class AllocationRequest(BaseModel):
    """Exactly the body of simulator `POST /v1/allocations`, minus idempotency_key (the orchestrator adds it)."""

    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: FuelType
    quantity: float = Field(gt=0)


class AlternativeAction(BaseModel):
    route_id: str
    source_depot_id: str
    expected_arrival_tick: int
    max_quantity: float
    rejected_because: str


class RecommendationImpact(BaseModel):
    """What sending this shipment changes over the forecast horizon, versus not sending it (earlier planned shipments included)."""

    horizon_ticks: int
    stockout_ticks_avoided: int = Field(description="Horizon ticks at zero inventory without the shipment minus with it.")
    unmet_liters_avoided: float = Field(description="Forecast demand that would go unserved without the shipment minus with it.")
    inventory_at_arrival_without: float
    inventory_at_arrival_with: float


ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNMEASURED"]


class ForecastConfidence(BaseModel):
    """How far to trust the forecast behind a recommendation. Derived only from measured forecast error, never a probability."""

    level: ConfidenceLevel
    error_mape: float | None = Field(default=None, description="Measured forecast error (Forecast.error_mape); null when unmeasured.")
    error_ticks: int = Field(description="Observed ticks that error was measured on.")
    error_band_liters: float | None = Field(default=None, description="Measured error applied to forecast demand between now and arrival.")
    shortfall_liters: float = Field(description="Safety stock minus projected inventory at arrival, without this shipment; 0 if none.")
    message: str


class Recommendation(BaseModel):
    id: str = Field(description="Deterministic: same NetworkState + Policy -> same id.")
    station_id: str
    fuel_type: FuelType
    priority: RiskLevel
    reason_codes: list[ReasonCode]
    request: AllocationRequest
    generated_tick: int
    expiry_tick: int = Field(description="Not executable after this tick; the orchestrator must replan.")
    dispatch_tick: int
    expected_arrival_tick: int
    current_inventory: float
    projected_inventory_at_arrival: float
    projected_safety_breach_tick: int | None
    projected_stockout_tick: int | None
    stress_margin_liters: float = Field(description="Projected inventory at arrival minus safety stock, without this shipment.")
    summary: str
    factors: list[str]
    alternatives: list[AlternativeAction]
    impact: RecommendationImpact
    confidence: ForecastConfidence
    planner_version: str


BlockedCode = Literal["UNREACHABLE", "DEPOT_EMPTY", "DEPOT_BELOW_RESERVE", "DISPATCH_FULL", "STATION_TANK_FULL", "BELOW_MIN_SHIPMENT"]


class BlockedCase(BaseModel):
    """A CRITICAL or HIGH station/fuel the planner could not send fuel to, and why. Never silently dropped."""

    station_id: str
    fuel_type: FuelType
    priority: RiskLevel
    code: BlockedCode
    message: str


class Plan(BaseModel):
    tick: int
    run_id: str
    planner_version: str = Field(description="Planner that produced the recommendations, e.g. 'order-up-to-v0' or 'fallback'.")
    forecasts: list[Forecast]
    projections: list[InventoryProjection]
    risks: list[RiskAssessment]
    recommendations: list[Recommendation]
    blocked: list[BlockedCase] = Field(default_factory=list, description="Urgent stations with no recommendation, and why.")
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Safety, health, and operator-facing API shapes
# ---------------------------------------------------------------------------

TripCode = Literal[
    "SNAPSHOT_STALE",
    "SNAPSHOT_TORN",
    "SIMULATOR_UNAVAILABLE",
    "RESET_UNCERTAIN",
    "EXECUTION_UNKNOWN",
    "PERSISTENCE_FAILURE",
    "RUN_ID_UNKNOWN",
    "RECOMMENDATION_EXPIRED",
    "RECOMMENDATION_CHANGED",
    "STATION_UNREACHABLE",
    "FORECAST_DRIFT",
    "PRIMARY_PLANNER_FAILED",
    "STATE_SYNC_SUSPECT",
    "HISTORY_GAP",
]
TripAction = Literal["ALERT", "REPLAN", "USE_FALLBACK", "REQUIRE_MANUAL_REVIEW", "FREEZE_AUTOMATION", "FULL_RESYNC"]


class Trip(BaseModel):
    code: TripCode
    severity: Literal["CRITICAL", "WARNING"]
    message: str
    scope: str = Field(description="What is affected: 'system', a station id, a recommendation id, ...")
    detected_tick: int | None
    required_actions: list[TripAction]


class TripwireStatus(BaseModel):
    state: Literal["CLEAR", "TRIPPED"] = Field(description="TRIPPED when any CRITICAL trip is active; blocks automatic execution.")
    trips: list[Trip]


ComponentStatus = Literal["HEALTHY", "DEGRADED", "DOWN", "UNKNOWN"]


class ComponentHealth(BaseModel):
    status: ComponentStatus
    detail: str | None = None


class SystemHealth(BaseModel):
    status: ComponentStatus = Field(description="Worst of the components.")
    components: dict[str, ComponentHealth] = Field(
        description="Keys: api, simulator, sse, snapshot, database, forecast, planner, tripwire, execution."
    )


OperatingMode = Literal["ADVISORY", "GUARDED_AUTO", "MANUAL_DEMO"]
RecommendationStatus = Literal["PROPOSED", "APPROVED", "EXECUTING", "DONE", "FAILED", "EXPIRED", "SUPERSEDED", "REJECTED"]


class RecommendationState(BaseModel):
    recommendation_id: str
    status: RecommendationStatus
    updated_tick: int
    allocation_id: int | None = None
    message: str | None = None
    allocation_status: AllocationStatus | None = Field(
        default=None, description="Observed lifecycle of the allocation this created (PENDING, IN_TRANSIT, ARRIVED, ...); null until one exists."
    )


class PlanApprovalItem(BaseModel):
    recommendation_id: str
    outcome: Literal["EXECUTED", "REFUSED"]
    status: RecommendationStatus | None = None
    code: str | None = Field(default=None, description="Refusal code when outcome is REFUSED.")
    message: str | None = None


class PlanApprovalResult(BaseModel):
    """Result of approving a whole plan in Manual Demo mode. Each recommendation was revalidated just before its POST."""

    items: list[PlanApprovalItem]
    stopped_early: bool = Field(description="True when a system-level refusal (kill switch, tripped guard, ...) ended the run.")


class AutomationState(BaseModel):
    mode: OperatingMode
    kill_switch: bool = Field(description="When true, nothing executes, in any mode.")


class DashboardResponse(BaseModel):
    """Everything the operator screen needs in one poll. `state`/`plan` are null when unavailable."""

    state: NetworkState | None
    plan: Plan | None
    tripwire: TripwireStatus
    health: SystemHealth
    automation: AutomationState
    recommendation_states: list[RecommendationState]
    last_trusted_tick: int | None
    last_trusted_age_seconds: float | None


class SimControlRequest(BaseModel):
    action: Literal["run", "pause", "step"]


class ErrorBody(BaseModel):
    code: str
    message: str
