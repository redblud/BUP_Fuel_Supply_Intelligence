import type { RiskAssessment } from '../api/types'
import { liters, title } from '../lib/format'

const order = { CRITICAL: 0, HIGH: 1, WATCH: 2, OK: 3 }

export function RiskPanel({ risks, planAvailable, onSelect }: { risks: RiskAssessment[]; planAvailable: boolean; onSelect: (key: string) => void }) {
  const sorted = [...risks].sort((a, b) => order[a.level] - order[b.level] || a.station_id.localeCompare(b.station_id))
  return <section className="panel" aria-labelledby="risk-title">
    <div className="panel-heading"><div><span className="eyebrow">PRIORITY</span><h2 id="risk-title">Stockout risks</h2></div><span className="count-pill">{sorted.filter((r) => r.level !== 'OK').length} active</span></div>
    {!planAvailable ? <p className="muted">Risk assessment unavailable.</p> : sorted.length === 0 ? <p className="muted">No assessed risks.</p> :
      <div className="risk-list">{sorted.map((risk) => <a className="risk-item" href={'#projection-' + risk.station_id + '-' + risk.fuel_type} onClick={() => onSelect(risk.station_id + '-' + risk.fuel_type)} key={risk.station_id + risk.fuel_type}>
        <span className={'severity severity-' + risk.level.toLowerCase()}>{risk.level}</span>
        <span className="risk-main"><strong>{title(risk.station_id)} · {risk.fuel_type}</strong><small>{risk.reason}</small><small>{liters(risk.current_inventory)} on hand · safety {liters(risk.safety_stock)}</small></span>
        <span className="risk-timing">{risk.projected_stockout_tick == null ? 'No stockout in horizon' : 'Stockout T' + risk.projected_stockout_tick}<small>{risk.projected_safety_breach_tick == null ? 'No breach' : 'Breach T' + risk.projected_safety_breach_tick}</small></span>
      </a>)}</div>}
  </section>
}

