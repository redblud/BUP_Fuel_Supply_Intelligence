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


class SimMetrics(BaseModel):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


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

    `demand_history` holds recent observations, oldest first.
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


class ForecastError(BaseModel):
    """Measured one-step forecast error over recent demand observations."""

    sample_count: int = Field(ge=0)
    mae_liters: float | None = Field(default=None, ge=0)
    rmse_liters: float | None = Field(default=None, ge=0)
    wape_percent: float | None = Field(default=None, ge=0)
    smape_percent: float | None = Field(default=None, ge=0)


class Forecast(BaseModel):
    """Forecast of demand_liters (not served_liters) per tick."""

    station_id: str
    fuel_type: FuelType
    start_tick: int
    liters_per_tick: list[float]
    calibration: float = Field(description="alpha in forecast = alpha x structural demand.")
    error: ForecastError = Field(description="Measured rolling error; unavailable for a cold start.")
    method: str


class ProjectionPoint(BaseModel):
    tick: int
    inventory: float
    incoming: float = 0.0
    demand: float = 0.0
    unmet_demand: float = 0.0


class InventoryProjection(BaseModel):
    station_id: str
    fuel_type: FuelType
    safety_stock: float
    minimum_projected_inventory: float
    projected_shortage_liters: float
    points: list[ProjectionPoint]


class DepotSupplyProjection(BaseModel):
    """Expected depot inventory after scheduled supply arrivals and crisis effects."""

    depot_id: str
    fuel_type: FuelType
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
    "FUEL_SCARCITY",
    "PROJECTED_SHORTAGE",
    "DEMAND_PRESSURE",
    "INCOMING_SUPPLY",
]


class RiskDriver(BaseModel):
    code: ReasonCode
    value: float | None = None
    unit: str | None = None
    threshold: float | None = None
    detail: str


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
    time_to_safety_breach_ticks: int | None = Field(description="Ticks from the snapshot to the first safety-stock breach.")
    time_to_stockout_ticks: int | None = Field(description="Ticks from the snapshot to projected zero inventory.")
    projected_shortage_liters: float = Field(description="Cumulative forecast demand that cannot be served over the horizon.")
    minimum_projected_inventory: float
    coverage_ticks: float | None = Field(description="Current inventory divided by average forecast demand per tick, before incoming supply.")
    coverage_hours: float | None
    earliest_arrival_tick: int | None = Field(description="Earliest tick a new shipment from a currently stocked, reachable depot could land.")
    redundancy: int = Field(description="Number of currently feasible routes to this station for this fuel.")
    risk_drivers: list[RiskDriver]
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
    planner_version: str


class Plan(BaseModel):
    tick: int
    run_id: str
    planner_version: str = Field(description="Planner that produced the recommendations, e.g. 'order-up-to-v0' or 'fallback'.")
    forecasts: list[Forecast]
    depot_projections: list[DepotSupplyProjection]
    projections: list[InventoryProjection]
    risks: list[RiskAssessment]
    recommendations: list[Recommendation]
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
    "STATION_UNREACHABLE",
    "FORECAST_DRIFT",
    "PRIMARY_PLANNER_FAILED",
    "STATE_SYNC_SUSPECT",
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
