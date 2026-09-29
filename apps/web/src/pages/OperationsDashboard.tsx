// First vertical slice: proves React -> /api/dashboard -> FastAPI -> simulator (or fixtures).
// Developer 3 grows this into the full operator console under src/features/*.
import type { ReactNode } from 'react'
import { useDashboard } from '../api/queries'
import { StatusDot } from '../components/status/StatusDot'
import { liters, title } from '../lib/format'

export function OperationsDashboard() {
  const { data, error, isPending } = useDashboard()

  if (isPending)
    return (
      <main>
        <h1>FuelOps AI</h1>
        <p>Connecting…</p>
      </main>
    )
  if (error || !data)
    return (
      <main>
        <h1>FuelOps AI</h1>
        <p>
          <StatusDot status="DOWN" /> Backend unreachable: {error?.message}
        </p>
      </main>
    )

  const { state, plan, tripwire, health, automation } = data
  const freshness = state?.meta.freshness ?? 'UNAVAILABLE'
  const component = (name: string) => health.components[name]

  return (
    <main>
      <header>
        <h1>FuelOps AI</h1>
        <span className="badge">SIMULATION</span>
      </header>

      <section className="card">
        <Row label="Backend">
          <StatusDot status={component('api')?.status ?? 'UNKNOWN'} />
        </Row>
        <Row label="Simulator">
          <StatusDot status={component('simulator')?.status ?? 'UNKNOWN'} /> {component('simulator')?.detail}
        </Row>
        <Row label="Database">
          <StatusDot status={component('database')?.status ?? 'UNKNOWN'} />
        </Row>
        <Row label="State">
          <StatusDot status={freshness} />
        </Row>
        <Row label="Tripwire">
          <StatusDot status={tripwire.state} />
        </Row>
        <Row label="Mode">
          {automation.mode}
          {automation.kill_switch ? ' · KILL SWITCH ON' : ''}
        </Row>
        <Row label="Current tick">
          {state ? state.run.tick : `— (last trusted ${data.last_trusted_tick ?? 'none'})`}
        </Row>
      </section>

      {tripwire.trips.length > 0 && (
        <section className="card">
          <h2>Trips</h2>
          <ul>
            {tripwire.trips.map((t) => (
              <li key={`${t.code}-${t.scope}`}>
                <b>{t.code}</b> ({t.severity}) {t.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {plan && (
        <>
          <section className="card">
            <h2>Recommendations ({plan.planner_version})</h2>
            {plan.recommendations.length === 0 && <p>No executable recommendation is currently available.</p>}
            {plan.recommendations.map((r) => (
              <article className="recommendation" key={r.id}>
                <div>
                  <b>{r.priority}</b> {title(r.station_id)} · {r.fuel_type}
                </div>
                <p className="summary">{r.summary}</p>
                <div className="meta-line">
                  Reason codes: {r.reason_codes.map(formatCode).join(' · ')}
                </div>
                <div className="meta-line">
                  Stress margin: <b>{liters(r.stress_margin_liters)}</b> · current inventory {liters(r.current_inventory)} · projected arrival inventory {liters(r.projected_inventory_at_arrival)}
                </div>
                <div className="impact-grid">
                  <div><span className="impact-label">Impact</span> {r.projected_stockout_ticks_avoided} stockout tick(s) avoided · {liters(r.projected_unmet_demand_liters_avoided)} unmet demand avoided</div>
                  <div><span className="impact-label">Arrival inventory</span> {liters(r.inventory_at_arrival_without_liters)} without → <b>{liters(r.inventory_at_arrival_with_liters)}</b> with shipment</div>
                  <div><span className="impact-label">Measured confidence</span> <b>{formatConfidence(r.confidence)}</b>{r.forecast_error_band_liters_per_tick != null ? ` · recent error band ${liters(r.forecast_error_band_liters_per_tick)}/tick` : ''}</div>
                  <div className="meta-line">{r.confidence_basis}</div>
                </div>
                <ul>
                  {r.factors.map((factor) => <li key={factor}>{factor}</li>)}
                </ul>
                {r.alternatives.length > 0 && (
                  <details>
                    <summary>Alternatives</summary>
                    <ul>
                      {r.alternatives.map((a) => (
                        <li key={a.route_id}>
                          {title(a.route_id)} from {title(a.source_depot_id)} · arrival tick {a.expected_arrival_tick} · max {liters(a.max_quantity)} · {a.rejected_because}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </article>
            ))}
          </section>

          {(plan.blocked_cases ?? []).length > 0 && (
            <section className="card">
              <h2>Blocked cases</h2>
              <ul>
                {(plan.blocked_cases ?? []).map((blocked) => (
                  <li key={blocked.id}>
                    <b>{formatCode(blocked.reason_code)}</b> · {title(blocked.station_id)} · {blocked.fuel_type}: {blocked.summary}
                    <ul>
                      {blocked.factors.map((factor) => <li key={factor}>{factor}</li>)}
                    </ul>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  )
}


function formatConfidence(confidence: string) {
  return confidence
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatCode(code: string) {
  return code
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="row">
      <span className="label">{label}</span>
      <span>{children}</span>
    </div>
  )
}
