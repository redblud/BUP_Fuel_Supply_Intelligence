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
        <section className="card">
          <h2>Recommendations ({plan.planner_version})</h2>
          {plan.recommendations.length === 0 && <p>No action needed.</p>}
          <ul>
            {plan.recommendations.map((r) => (
              <li key={r.id}>
                <b>{r.priority}</b> {title(r.station_id)} {r.fuel_type}: {r.summary}{' '}
                <small>(stress margin {liters(r.stress_margin_liters)})</small>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="row">
      <span className="label">{label}</span>
      <span>{children}</span>
    </div>
  )
}
