
import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { NetworkState, Plan } from '../api/types'
import { liters, title } from '../lib/format'

export function ProjectionPanel({ plan, state, selectedKey, onSelect }: { plan: Plan | null; state: NetworkState | null; selectedKey?: string; onSelect: (key: string) => void }) {

  const projections = plan?.projections ?? []
  const key = selectedKey || (projections[0] ? projections[0].station_id + '-' + projections[0].fuel_type : '')
  const projection = projections.find((item) => item.station_id + '-' + item.fuel_type === key) ?? projections[0]
  const risk = plan?.risks.find((item) => item.station_id === projection?.station_id && item.fuel_type === projection?.fuel_type)
  const recommendation = plan?.recommendations.find((item) => item.station_id === projection?.station_id && item.fuel_type === projection?.fuel_type)
  const incoming = projection?.points.filter((point) => point.incoming > 0) ?? []
  const shipments = state?.allocations.filter((item) => item.destination_station_id === projection?.station_id && item.fuel_type === projection?.fuel_type && item.status === 'IN_TRANSIT') ?? []

  return <section className="panel" id={projection ? 'projection-' + projection.station_id + '-' + projection.fuel_type : undefined} aria-labelledby="projection-title">
    <div className="panel-heading"><div><span className="eyebrow">FORECAST</span><h2 id="projection-title">Inventory projection</h2></div></div>
    {!plan ? <p className="muted">Projection unavailable.</p> : projections.length === 0 ? <p className="muted">No projection data.</p> : <>
      <label className="field-label" htmlFor="projection-select">Station and fuel</label>
      <select id="projection-select" value={key} onChange={(event) => onSelect(event.target.value)}>{projections.map((item) => <option key={item.station_id + item.fuel_type} value={item.station_id + '-' + item.fuel_type}>{title(item.station_id)} · {item.fuel_type}</option>)}</select>
      {projection ? <>
        <div className="chart-wrap" role="img" aria-label={'Projected ' + projection.fuel_type + ' inventory for ' + title(projection.station_id)}>
          <ResponsiveContainer width="100%" height={235}><LineChart data={projection.points} margin={{ top: 12, right: 14, left: 0, bottom: 3 }}>
            <CartesianGrid stroke="#2b3c51" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="tick" type="number" domain={['dataMin', 'dataMax']} stroke="#90a3b8" tick={{ fontSize: 11 }} />
            <YAxis stroke="#90a3b8" tick={{ fontSize: 11 }} width={50} tickFormatter={(value: number) => Math.round(value / 1000) + 'k'} />
            <Tooltip formatter={(value, name) => [liters(Number(value)), String(name)]} labelFormatter={(tick) => 'Tick ' + tick} contentStyle={{ background: '#142238', borderColor: '#41546c', color: '#fff' }} />
            <ReferenceLine y={projection.safety_stock} stroke="#f5ae66" strokeDasharray="5 4" label={{ value: 'Safety', fill: '#f5ae66', fontSize: 10, position: 'insideTopRight' }} />
            {recommendation ? <ReferenceLine x={recommendation.expected_arrival_tick} stroke="#56d2c1" strokeDasharray="3 4" label={{ value: 'Plan arrival', fill: '#56d2c1', fontSize: 10 }} /> : null}
            <Line type="monotone" dataKey="inventory" stroke="#59d3bb" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
            {incoming.map((point) => <ReferenceDot key={point.tick} x={point.tick} y={point.inventory} r={5} fill="#6bb8ff" stroke="#11243b" />)}
          </LineChart></ResponsiveContainer>
        </div>
        <div className="chart-legend"><span><i className="legend-inventory" /> Inventory</span><span><i className="legend-safety" /> Safety stock</span><span><i className="legend-incoming" /> Incoming supply</span></div>
        <div className="projection-summary"><span>Safety breach <strong>{risk?.projected_safety_breach_tick == null ? 'None' : 'T' + risk.projected_safety_breach_tick}</strong></span><span>Stockout <strong>{risk?.projected_stockout_tick == null ? 'None' : 'T' + risk.projected_stockout_tick}</strong></span><span>Earliest arrival <strong>{risk?.earliest_arrival_tick == null ? 'Unreachable' : 'T' + risk.earliest_arrival_tick}</strong></span></div>
        {incoming.length > 0 ? <p className="chart-caption">Incoming shipments: {incoming.map((point) => liters(point.incoming) + ' at T' + point.tick).join(', ')}</p> : null}
        {shipments.length > 0 ? <p className="chart-caption">In transit: {shipments.map((item) => '#' + item.id + ' expected T' + (item.expected_arrival_tick ?? '?')).join(', ')}</p> : null}
      </> : null}
    </>}
  </section>
}


