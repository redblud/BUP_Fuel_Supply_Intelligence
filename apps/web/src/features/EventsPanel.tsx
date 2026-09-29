import type { NetworkState, RiskAssessment, TripwireStatus } from '../api/types'
import { title } from '../lib/format'

type Trip = TripwireStatus['trips'][number]

export function EventsPanel({ state, risks, trips }: { state: NetworkState | null; risks: RiskAssessment[]; trips: Trip[] }) {
  const alerts = [
    ...trips.filter((trip) => trip.severity === 'WARNING').map((trip) => ({ key: 'trip-' + trip.code + trip.scope, tick: trip.detected_tick ?? -1, label: trip.code, detail: trip.message, kind: 'WARNING' })),
    ...risks.filter((risk) => risk.level === 'CRITICAL').map((risk) => ({ key: 'risk-' + risk.station_id + risk.fuel_type, tick: risk.projected_stockout_tick ?? -1, label: 'CRITICAL · ' + title(risk.station_id), detail: risk.reason, kind: 'CRITICAL' })),
  ].sort((a, b) => b.tick - a.tick)
  const events = [...(state?.events ?? [])].sort((a, b) => b.start_tick - a.start_tick)
  return <section className="panel" aria-labelledby="events-title">
    <div className="panel-heading"><div><span className="eyebrow">SITUATION</span><h2 id="events-title">Alerts and events</h2></div><span className="count-pill">{alerts.length + events.length}</span></div>
    {alerts.length > 0 ? <div className="event-group"><h3>Alerts</h3>{alerts.map((alert) => <div className="event-item" key={alert.key}><span className={'severity severity-' + alert.kind.toLowerCase()}>{alert.kind}</span><div><strong>{title(alert.label)}</strong><p>{alert.detail}</p></div></div>)}</div> : null}
    {events.length > 0 ? <div className="event-group"><h3>Simulator events</h3>{events.map((event) => <div className="event-item" key={event.id}><span className={'event-status event-' + event.status.toLowerCase()}>{event.status}</span><div><strong>{title(event.type)}</strong><p>Ticks {event.start_tick}–{event.end_tick} · {Object.entries(event.parameters ?? {}).map(([key, value]) => title(key) + ': ' + String(value)).join(', ') || 'No affected entities recorded'}</p></div></div>)}</div> : null}
    {alerts.length === 0 && events.length === 0 ? <p className="muted">{state ? 'No active alerts or recorded events.' : 'Event data unavailable.'}</p> : null}
  </section>
}

