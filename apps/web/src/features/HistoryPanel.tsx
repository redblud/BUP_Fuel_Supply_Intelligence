import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { DashboardResponse } from '../api/types'
import { liters, title } from '../lib/format'

export function HistoryPanel({ data }: { data: DashboardResponse }) {
  const runId = data.state?.meta.run_id
  const tick = data.state?.run.tick
  const decisionsQuery = useQuery({ queryKey: ['decisions', runId, tick], queryFn: api.decisions, enabled: !!runId, retry: false })
  const allocationsQuery = useQuery({ queryKey: ['allocations', runId, tick], queryFn: api.allocations, enabled: !!runId, retry: false })
  const decisions = decisionsQuery.data ?? data.recommendation_states
  const allocations = allocationsQuery.data ?? []
  const liveAllocations = data.state?.allocations ?? []
  return <section className="panel" aria-labelledby="history-title">
    <div className="panel-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h2 id="history-title">Decisions and allocations</h2></div></div>
    {decisions.length === 0 && allocations.length === 0 && liveAllocations.length === 0 ? <p className="muted">No decisions or allocations recorded.</p> : <>
      {decisions.length > 0 ? <div className="table-scroll"><table><caption>Recommendation decisions</caption><thead><tr><th>Recommendation</th><th>Status</th><th>Allocation</th><th>Tick</th></tr></thead><tbody>{decisions.map((item) => <tr key={item.recommendation_id}><td title={item.recommendation_id}>{item.recommendation_id.slice(0, 12)}…</td><td>{item.status}</td><td>{item.allocation_id ?? '—'}</td><td>{item.updated_tick}</td></tr>)}</tbody></table></div> : null}
      <div className="table-scroll"><table><caption>Simulator allocations</caption><thead><tr><th>ID</th><th>Route</th><th>Fuel</th><th>Quantity</th><th>Progress</th></tr></thead><tbody>{(allocations.length > 0 ? allocations.map((item) => item.allocation) : liveAllocations).map((item) => <tr key={item.id}><td>#{item.id}</td><td>{title(item.source_depot_id)} → {title(item.destination_station_id)}</td><td>{item.fuel_type}</td><td>{liters(item.quantity)}</td><td>{item.status}{item.expected_arrival_tick == null ? '' : ' · ETA T' + item.expected_arrival_tick}</td></tr>)}</tbody></table></div>
    </>}
    {decisionsQuery.isError || allocationsQuery.isError ? <p className="muted">History service unavailable. Showing current snapshot where possible.</p> : null}
  </section>
}
