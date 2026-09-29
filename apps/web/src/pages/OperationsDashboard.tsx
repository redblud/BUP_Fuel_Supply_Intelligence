import { useState } from 'react'
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowRight, CheckCircle2, Clock3,
  Database, Fuel, MapPinned, PackageCheck, Route, ShieldAlert, ShieldCheck, Truck,
} from 'lucide-react'
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useDashboard } from '../api/queries'
import type { NetworkState, Recommendation, RiskAssessment } from '../api/types'
import { liters } from '../lib/format'

type FuelType = 'DIESEL' | 'PETROL' | 'OCTANE'
const fuels: FuelType[] = ['DIESEL', 'PETROL', 'OCTANE']
const rank = { CRITICAL: 0, HIGH: 1, WATCH: 2, OK: 3 }

function entityName(id: string, state: NetworkState) {
  return [...state.stations, ...state.depots, ...state.regions].find((item) => item.id === id)?.name
    ?? id.replace(/^(station|depot|region)-/, '').replaceAll('-', ' ')
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Fuel size={21} /></span><span>carbon<b>Track</b></span></div>
      <nav className="sidebar-nav" aria-label="Dashboard sections"><span className="sidebar-label">WORKSPACE</span><a className="nav-item active" href="#top"><Activity size={17} /> Overview</a><a className="nav-item" href="#outlook-heading"><Route size={17} /> Supply outlook</a><a className="nav-item" href="#inventory-heading"><Fuel size={17} /> Station inventory</a><a className="nav-item" href="#recommendation-heading"><Truck size={17} /> Recommendations</a></nav>
      <div className="sidebar-bottom"><span className="sim-label"><i /> SIMULATION ENVIRONMENT</span><p>Operational decision support<br />for the BUP fuel network</p></div>
    </aside>
    <div className="app-main">{children}</div>
  </div>
}

function Metric({ icon, label, value, detail, alert = false }: { icon: React.ReactNode; label: string; value: string; detail: string; alert?: boolean }) {
  return <div className={`metric ${alert ? 'metric-alert' : ''}`}><div className="metric-label">{label}{icon}</div><strong>{value}</strong><small>{detail}</small></div>
}

function StatusPill({ value }: { value: string }) {
  return <span className={`status-pill status-${value.toLowerCase()}`}>{value.toLowerCase()}</span>
}

function RiskRow({ risk, state, selected, onSelect }: { risk: RiskAssessment; state: NetworkState; selected: boolean; onSelect: () => void }) {
  const stockout = risk.projected_stockout_tick
  const tick = stockout ?? risk.projected_safety_breach_tick
  return <button type="button" className={`risk-row ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={onSelect}>
    <span className={`risk-level risk-${risk.level.toLowerCase()}`}><i />{risk.level.toLowerCase()}</span>
    <div className="risk-copy"><strong>{entityName(risk.station_id, state)} <span>· {risk.fuel_type.toLowerCase()}</span></strong><p>{risk.reason}</p></div>
    <div className="risk-time"><strong>{tick === null ? 'Unknown' : tick <= state.run.tick ? `Tick ${tick}` : `${tick - state.run.tick} ticks away`}</strong><span>{stockout === null ? 'safety stock breach' : 'projected stockout'}</span></div>
  </button>
}

function RecommendationRow({ item, state, trusted }: { item: Recommendation; state: NetworkState; trusted: boolean }) {
  const [expanded, setExpanded] = useState(false)
  return <div className="recommendation-row">
    <div className="recommendation-top">
      <span className="truck-icon"><Truck size={17} /></span>
      <div className="recommendation-title"><strong>{entityName(item.request.source_depot_id, state)} <ArrowRight size={14} /> {entityName(item.station_id, state)}</strong><span>{item.fuel_type.toLowerCase()} · {liters(item.request.quantity)} · arrival tick {item.expected_arrival_tick}</span></div>
      <StatusPill value={item.priority} />
    </div>
    <p>{item.summary}</p>
    <button className="detail-button" type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Hide details' : 'Why this action'} <ArrowRight size={14} /></button>
    {expanded && <div className="recommendation-details">
      <div><span>Current stock</span><strong>{liters(item.current_inventory)}</strong></div>
      <div><span>Stress margin</span><strong>{liters(item.stress_margin_liters)}</strong></div>
      <div><span>Valid through</span><strong>Tick {item.expiry_tick}</strong></div>
      <div><span>Alternatives</span><strong>{item.alternatives.length} considered</strong></div>
      <p>{item.factors.join(' · ') || 'No additional factors recorded.'}</p>
      {!trusted && <p className="warning-text">Snapshot is untrusted. Treat this proposal as reference only.</p>}
    </div>}
  </div>
}

export function OperationsDashboard() {
  const { data, error, isPending, isFetching, refetch } = useDashboard()
  const [fuel, setFuel] = useState<FuelType>('DIESEL')
  const [stationId, setStationId] = useState('')

  if (isPending) return <Shell><div className="full-state"><Activity size={30} /><h1>Connecting to CarbonTrack</h1><p>Loading the network snapshot and decision signals.</p></div></Shell>
  if (!data) return <Shell><div className="full-state"><ShieldAlert size={30} /><h1>Dashboard unavailable</h1><p>{error?.message ?? 'The API could not be reached.'}</p><button className="retry-button" onClick={() => void refetch()}>Retry connection</button></div></Shell>

  const { state, plan, tripwire, health, automation } = data
  const trusted = Boolean(state && (state.meta.freshness === 'FRESH' || state.meta.freshness === 'FIXTURE') && !state.meta.stale && state.meta.consistent)
  const risks = [...(plan?.risks ?? [])].sort((a, b) => rank[a.level] - rank[b.level])
  const highRisks = risks.filter((risk) => rank[risk.level] < 2)
  const recommendations = [...(plan?.recommendations ?? [])].sort((a, b) => rank[a.priority] - rank[b.priority])
  const upcoming = state?.supply_arrivals.filter((arrival) => arrival.status !== 'ARRIVED').sort((a, b) => a.planned_tick - b.planned_tick).slice(0, 4) ?? []
  const activeEvents = state?.events.filter((event) => event.status === 'ACTIVE') ?? []
  const disruptedRoutes = state?.routes.filter((route) => route.status === 'DISRUPTED') ?? []
  const freshness = !state ? 'No snapshot' : state.meta.stale ? 'Stale snapshot' : trusted ? state.meta.freshness === 'FIXTURE' ? 'Fixture data' : 'Live snapshot' : `${state.meta.freshness.toLowerCase().replace('_', ' ')} snapshot`
  const selectedStationId = stationId && state?.stations.some((station) => station.id === stationId)
    ? stationId : highRisks[0]?.station_id ?? state?.stations[0]?.id ?? ''
  const projection = plan?.projections.find((item) => item.station_id === selectedStationId && item.fuel_type === fuel)
  const forecast = plan?.forecasts.find((item) => item.station_id === selectedStationId && item.fuel_type === fuel)
  const selectedRisk = risks.find((item) => item.station_id === selectedStationId && item.fuel_type === fuel)
  const shipmentTicks = [...new Set(state?.allocations.filter((item) => item.destination_station_id === selectedStationId && item.fuel_type === fuel && (item.status === 'PENDING' || item.status === 'IN_TRANSIT')).map((item) => item.expected_arrival_tick).filter((tick): tick is number => tick !== null) ?? [])]
  const recommendedTicks = [...new Set(recommendations.filter((item) => item.station_id === selectedStationId && item.fuel_type === fuel).map((item) => item.expected_arrival_tick))]
  const observed = state?.demand_history.filter((item) => item.station_id === selectedStationId && item.fuel_type === fuel).slice(-16) ?? []
  const demandPoints = [
    ...observed.map((item) => ({ tick: item.tick, observed: Math.round(item.demand_liters), forecast: null as number | null })),
    ...(forecast?.liters_per_tick.slice(0, 8).map((value, index) => ({ tick: forecast.start_tick + index, observed: null as number | null, forecast: Math.round(value) })) ?? []),
  ]

  return <Shell>
    <header className="topbar"><div className="breadcrumb">Operations <ArrowRight size={13} /> <strong>Network overview</strong></div><div className="topbar-meta"><span className={`freshness ${trusted ? 'trusted' : ''}`}><i /> {freshness}</span><span className="topbar-clock">{state ? new Date(state.meta.retrieved_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Awaiting data'}</span></div></header>
    <main className="dashboard" id="top">
      <div className="page-heading"><div><h1>Fuel network overview</h1><p>Monitor supply, spot shortages, and inspect proposed allocations.</p></div><div className="scenario">SCENARIO <strong>{state?.run.scenario_id ?? 'Unavailable'}</strong><span>·</span><strong>Tick {state?.run.tick ?? data.last_trusted_tick ?? '—'}</strong></div></div>

      {(!trusted || tripwire.state === 'TRIPPED') && <div className="trust-banner" role="alert"><ShieldAlert size={21} /><div><strong>Automatic execution paused · {trusted ? 'safety guard tripped' : 'simulator data untrusted'}</strong><span>{!state ? 'No network snapshot is available. Check simulator and API health below.' : !trusted ? `Showing the last available state (${state.meta.stale ? 'stale' : state.meta.freshness.toLowerCase().replace('_', ' ')}). Inventory and projections may be out of date.` : tripwire.trips.find((trip) => trip.severity === 'CRITICAL')?.message ?? 'A critical safety condition is active.'} Last trusted tick {data.last_trusted_tick ?? 'none'}{data.last_trusted_age_seconds === null ? '' : ` · ${Math.round(data.last_trusted_age_seconds)}s ago`}.</span></div></div>}
      {error && <div className="trust-banner" role="alert"><AlertTriangle size={20} /><div><strong>Refresh failed</strong><span>{error.message}. Values below are from the last successful API response.</span></div></div>}

      <div className="metrics">
        <Metric icon={<Fuel size={19} />} label="Station inventory" value={state ? liters(state.stations.reduce((total, station) => total + fuels.reduce((sum, key) => sum + (station.inventory[key] ?? 0), 0), 0)) : '—'} detail="Across all stations" />
        <Metric icon={<Activity size={19} />} label="Service level" value={state?.metrics ? `${(state.metrics.service_level * 100).toFixed(1)}%` : '—'} detail={state?.metrics ? `${liters(state.metrics.unmet_demand_liters)} unmet demand` : 'Metrics unavailable'} />
        <Metric icon={<AlertTriangle size={19} />} label="High risk signals" value={plan ? String(highRisks.length) : '—'} detail={plan ? `${risks.filter((risk) => risk.level === 'CRITICAL').length} critical · ${risks.filter((risk) => risk.level === 'HIGH').length} high` : 'Forecast unavailable'} alert={highRisks.length > 0} />
        <Metric icon={<Truck size={19} />} label="Incoming supply" value={state ? String(upcoming.length) : '—'} detail="Next depot arrivals" />
      </div>

      <section className="chart-section" aria-labelledby="outlook-heading">
        <div className="chart-section-heading"><div><h2 id="outlook-heading">Supply outlook</h2><p>Explore how demand and inventory change for one station and fuel.</p></div><div className="chart-controls"><label htmlFor="chart-station">Station</label><select id="chart-station" value={selectedStationId} onChange={(event) => setStationId(event.target.value)} disabled={!state}>{state?.stations.map((station) => <option key={station.id} value={station.id}>{station.name}</option>)}</select><label htmlFor="chart-fuel">Fuel</label><select id="chart-fuel" value={fuel} onChange={(event) => setFuel(event.target.value as FuelType)}>{fuels.map((item) => <option key={item} value={item}>{item.toLowerCase()}</option>)}</select></div></div>
        <div className="charts">
          <div className="chart-panel"><div className="chart-title"><div><h3>Projected inventory</h3><p>Liters at station · safety stock {projection ? liters(projection.safety_stock) : 'unavailable'}</p></div><span className="chart-key"><i className="inventory-key" /> Inventory</span></div>{selectedRisk && <p className="chart-risk">{selectedRisk.projected_stockout_tick !== null ? `Stockout in ${Math.max(0, selectedRisk.projected_stockout_tick - (state?.run.tick ?? 0))} ticks` : selectedRisk.projected_safety_breach_tick !== null ? `Safety breach in ${Math.max(0, selectedRisk.projected_safety_breach_tick - (state?.run.tick ?? 0))} ticks` : 'No breach projected'}</p>}{projection ? <div className="chart-frame" role="img" aria-label={`Projected ${fuel.toLowerCase()} inventory for ${state ? entityName(selectedStationId, state) : 'station'} over ${projection.points.length} ticks; ${shipmentTicks.length} incoming shipments and ${recommendedTicks.length} recommended arrivals`}><ResponsiveContainer width="100%" height="100%"><AreaChart data={projection.points} margin={{ top: 8, right: 8, left: -14, bottom: 0 }}><CartesianGrid stroke="#edf2f0" vertical={false} /><XAxis dataKey="tick" tickLine={false} axisLine={false} tick={{ fill: '#879c9e', fontSize: 10 }} tickMargin={8} /><YAxis tickLine={false} axisLine={false} tick={{ fill: '#879c9e', fontSize: 10 }} tickFormatter={(value: number) => `${Math.round(value / 1000)}k`} /><Tooltip formatter={(value) => liters(Number(value))} labelFormatter={(value) => `Tick ${value}`} /><ReferenceLine y={projection.safety_stock} stroke="#d99853" strokeDasharray="5 4" />{shipmentTicks.map((tick) => <ReferenceLine key={`shipment-${tick}`} x={tick} stroke="#6b91b0" strokeDasharray="3 3" />)}{recommendedTicks.map((tick) => <ReferenceLine key={`plan-${tick}`} x={tick} stroke="#8879ac" strokeDasharray="4 3" />)}{selectedRisk?.projected_stockout_tick !== null && selectedRisk?.projected_stockout_tick !== undefined && <ReferenceLine x={selectedRisk.projected_stockout_tick} stroke="#c86555" strokeDasharray="2 3" />}<Area type="monotone" dataKey="inventory" stroke="#299b81" strokeWidth={2.5} fill="#dff2ea" dot={false} activeDot={{ r: 4 }} /></AreaChart></ResponsiveContainer></div> : <p className="empty">Inventory projection unavailable.</p>}<div className="chart-caption"><span><i className="safety-key" /> Safety stock</span><span><i className="shipment-key" /> {shipmentTicks.length} incoming</span><span><i className="plan-key" /> {recommendedTicks.length} planned</span></div></div>
          <div className="chart-panel"><div className="chart-title"><div><h3>Demand trend</h3><p>Observed demand and the next eight forecast ticks</p></div><div className="chart-legend"><span><i className="observed-key" /> Observed</span><span><i className="forecast-key" /> Forecast</span></div></div>{demandPoints.length ? <div className="chart-frame" role="img" aria-label={`Recent observed and forecast ${fuel.toLowerCase()} demand for ${state ? entityName(selectedStationId, state) : 'station'}`}><ResponsiveContainer width="100%" height="100%"><LineChart data={demandPoints} margin={{ top: 8, right: 8, left: -14, bottom: 0 }}><CartesianGrid stroke="#edf2f0" vertical={false} /><XAxis dataKey="tick" tickLine={false} axisLine={false} tick={{ fill: '#879c9e', fontSize: 10 }} tickMargin={8} /><YAxis tickLine={false} axisLine={false} tick={{ fill: '#879c9e', fontSize: 10 }} /><Tooltip formatter={(value) => `${Math.round(Number(value))} L / tick`} labelFormatter={(value) => `Tick ${value}`} /><Line type="monotone" dataKey="observed" stroke="#2f8b79" strokeWidth={2.5} dot={false} connectNulls={false} /><Line type="monotone" dataKey="forecast" stroke="#d99853" strokeWidth={2.5} strokeDasharray="5 4" dot={false} connectNulls={false} /></LineChart></ResponsiveContainer></div> : <p className="empty">Demand observations and forecast unavailable.</p>}<div className="chart-caption"><span>Demand liters per simulated tick</span><span>{observed.length} observed ticks</span></div></div>
        </div>
      </section>

      <div className="content-grid">
        <div className="main-column">
          <section className="panel" aria-labelledby="risk-heading"><div className="panel-heading"><div><h2 id="risk-heading">Priority watch</h2><p>Projected shortages and the signals behind them.</p></div><span>{highRisks.length} requiring attention</span></div>
            {!plan ? <p className="empty">Risk assessment is unavailable until planning succeeds.</p> : !state || highRisks.length === 0 ? <div className="clear-message"><CheckCircle2 size={20} /> No high risk shortages projected</div> : <div className="risk-list">{highRisks.slice(0, 4).map((risk) => <RiskRow key={`${risk.station_id}-${risk.fuel_type}`} risk={risk} state={state} selected={risk.station_id === selectedStationId && risk.fuel_type === fuel} onSelect={() => { setStationId(risk.station_id); setFuel(risk.fuel_type) }} />)}{highRisks.length > 4 && <p className="more-note">+{highRisks.length - 4} more high risk signals</p>}</div>}
          </section>

          <section className="panel" aria-labelledby="inventory-heading"><div className="panel-heading inventory-heading"><div><h2 id="inventory-heading">Station inventory</h2><p>Liters on hand against storage capacity.</p></div><div className="fuel-selector" role="group" aria-label="Fuel type">{fuels.map((item) => <button key={item} className={fuel === item ? 'selected' : ''} aria-pressed={fuel === item} onClick={() => setFuel(item)}>{item.toLowerCase()}</button>)}</div></div>
            {!state ? <p className="empty">Station inventory will appear when a snapshot is available.</p> : <div className="table-scroll"><table><thead><tr><th>Station</th><th>Region</th><th>Inventory</th><th>Capacity used</th><th>Status</th></tr></thead><tbody>{state.stations.map((station) => { const inventory = station.inventory[fuel] ?? 0; const capacity = station.capacity[fuel] ?? 0; const percent = capacity ? Math.min(100, inventory / capacity * 100) : 0; const risk = risks.find((item) => item.station_id === station.id && item.fuel_type === fuel); return <tr key={station.id}><td className="table-strong">{station.name}</td><td>{entityName(station.region_id, state)}</td><td className="table-strong">{liters(inventory)}</td><td><div className="capacity"><span className={`capacity-track ${risk && rank[risk.level] < 2 ? 'low' : ''}`}><i style={{ width: `${percent}%` }} /></span>{Math.round(percent)}%</div></td><td><StatusPill value={station.status === 'OUTAGE' ? 'OUTAGE' : risk?.level ?? 'OK'} /></td></tr> })}</tbody></table></div>}
          </section>

          <section className="panel" aria-labelledby="recommendation-heading"><div className="panel-heading"><div><h2 id="recommendation-heading">Recommended allocations</h2><p>{trusted ? 'Advisory proposals from this planning run.' : 'Reference only while the snapshot is untrusted.'}</p></div><span>{recommendations.length} proposals</span></div>
            {!plan ? <p className="empty">Recommendations are unavailable while planning has no snapshot.</p> : recommendations.length === 0 ? <p className="empty">No allocation is recommended for this tick.</p> : state && <div className="recommendation-list">{recommendations.slice(0, 5).map((item) => <RecommendationRow key={item.id} item={item} state={state} trusted={trusted} />)}</div>}
            {plan && <div className="panel-footer"><span>Planner: {plan.planner_version}</span><span>Generated at tick {plan.tick}</span></div>}
          </section>
        </div>

        <div className="side-column">
          <section className={`safety ${tripwire.state === 'TRIPPED' ? 'tripped' : ''}`} aria-labelledby="safety-heading">{tripwire.state === 'CLEAR' ? <ShieldCheck size={23} /> : <ShieldAlert size={23} />}<div><h2 id="safety-heading">{tripwire.state === 'CLEAR' ? 'Safety guard clear' : 'Safety guard tripped'}</h2><p>{tripwire.state === 'CLEAR' ? 'No critical safety condition is active.' : 'Automatic execution is blocked until the active issues are resolved.'}</p><strong>{automation.mode.replace('_', ' ')}{automation.kill_switch ? ' · Kill switch on' : ''}</strong></div></section>
          {tripwire.trips.length > 0 && <section className="panel compact-panel"><div className="panel-heading"><div><h2>Active safety alerts</h2><p>Review before acting.</p></div><ShieldAlert size={19} /></div><div className="event-list">{tripwire.trips.slice(0, 4).map((trip) => <div className="event-row" key={`${trip.code}-${trip.scope}`}><i /><div><strong>{trip.code.replaceAll('_', ' ')}</strong><span>{trip.message}</span></div></div>)}</div></section>}
          <section className="panel compact-panel" aria-labelledby="supply-heading"><div className="panel-heading"><div><h2 id="supply-heading">Incoming supply</h2><p>Next deliveries to depots.</p></div><PackageCheck size={19} /></div>{!state || upcoming.length === 0 ? <p className="empty">{state ? 'No upcoming deliveries scheduled.' : 'Supply schedule unavailable.'}</p> : <div className="supply-list">{upcoming.map((arrival) => <div className="supply-row" key={arrival.id}><span className="supply-icon"><ArrowDownRight size={17} /></span><div><strong>{liters(arrival.quantity)} {arrival.fuel_type.toLowerCase()}</strong><span>{entityName(arrival.depot_id, state)}</span></div><div className="supply-time"><strong>Tick {arrival.planned_tick}</strong><span className={arrival.status === 'DELAYED' ? 'delayed' : ''}>{arrival.status.toLowerCase()}</span></div></div>)}</div>}</section>
          <section className="panel compact-panel" aria-labelledby="events-heading"><div className="panel-heading"><div><h2 id="events-heading">Disruptions</h2><p>Active events and route constraints.</p></div><MapPinned size={19} /></div>{!state ? <p className="empty">Event status unavailable.</p> : activeEvents.length === 0 && disruptedRoutes.length === 0 ? <div className="clear-message"><CheckCircle2 size={18} /> No active disruptions</div> : <div className="event-list">{activeEvents.map((event) => <div className="event-row" key={event.id}><i /><div><strong>{event.type.replaceAll('_', ' ')}</strong><span>Active through tick {event.end_tick}</span></div></div>)}{disruptedRoutes.map((route) => <div className="event-row" key={route.id}><i /><div><strong>Route disrupted</strong><span>{entityName(route.source_depot_id, state)} → {entityName(route.destination_station_id, state)}</span></div></div>)}</div>}</section>
          <section className="panel compact-panel" aria-labelledby="health-heading"><div className="panel-heading"><div><h2 id="health-heading">System health</h2><p>Components supporting this view.</p></div><Database size={19} /></div><div className="health-list">{['api', 'simulator', 'sse', 'snapshot', 'database', 'forecast', 'planner', 'tripwire', 'execution'].map((key) => <div className="health-row" key={key}><span>{key.toUpperCase()}</span><span className={`health-value ${health.components[key]?.status.toLowerCase() ?? 'unknown'}`}><i />{health.components[key]?.status.toLowerCase() ?? 'unknown'}</span></div>)}</div></section>
        </div>
      </div>
      <footer><span>CarbonTrack monitors the BUP fuel supply simulation.</span><span><Clock3 size={14} /> Refreshes every 2 seconds {isFetching ? '· updating' : ''}</span></footer>
    </main>
  </Shell>
}
