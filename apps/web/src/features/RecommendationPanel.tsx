import type { Recommendation, DashboardResponse } from '../api/types'
import { liters, title } from '../lib/format'

type RecommendationState = DashboardResponse['recommendation_states'][number]

export function RecommendationPanel({ recommendations, states, planAvailable, trusted }: { recommendations: Recommendation[]; states: RecommendationState[]; planAvailable: boolean; trusted: boolean }) {
  const stateById = new Map(states.map((state) => [state.recommendation_id, state]))
  return <section className="panel" aria-labelledby="plan-title">
    <div className="panel-heading"><div><span className="eyebrow">CURRENT PLAN</span><h2 id="plan-title">Recommendations</h2></div><span className="count-pill">{recommendations.length}</span></div>
    {!planAvailable ? <p className="muted">Plan unavailable. No new action is recommended.</p> : recommendations.length === 0 ? <p className="muted">No replenishment action needed.</p> :
      <div className="recommendation-list">{recommendations.map((rec) => <article className="recommendation" key={rec.id}>
        <div className="recommendation-top"><span className={'severity severity-' + rec.priority.toLowerCase()}>{rec.priority}</span><span className="muted">{stateById.get(rec.id)?.status ?? 'PROPOSED'}</span></div>
        <h3>{title(rec.station_id)} · {rec.fuel_type}</h3>
        <p>{rec.summary}</p>
        <div className="recommendation-metrics"><div><span>Send</span><strong>{liters(rec.request.quantity)}</strong></div><div><span>Arrival</span><strong>Tick {rec.expected_arrival_tick}</strong></div><div><span>Expires</span><strong>Tick {rec.expiry_tick}</strong></div></div>
        <p className="route-line">{title(rec.request.source_depot_id)} → {title(rec.request.route_id)} → {title(rec.station_id)}</p>
        {!trusted ? <p className="danger-text">Snapshot untrusted. Validate before acting.</p> : null}
        <details><summary>Why this recommendation?</summary>
          <div className="explanation"><p><strong>Stress margin:</strong> {liters(rec.stress_margin_liters)}</p><p><strong>Reason codes:</strong> {rec.reason_codes.join(', ') || 'None'}</p>
          <ul>{rec.factors.map((factor) => <li key={factor}>{factor}</li>)}</ul>
          {rec.alternatives.length > 0 ? <><strong>Alternatives considered</strong><ul>{rec.alternatives.map((alt) => <li key={alt.route_id}>{title(alt.source_depot_id)} via {title(alt.route_id)}, arrival T{alt.expected_arrival_tick}: {alt.rejected_because}</li>)}</ul></> : <p>No alternative route was recorded.</p>}
          </div>
        </details>
      </article>)}</div>}
  </section>
}
