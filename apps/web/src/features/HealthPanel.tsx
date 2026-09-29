import type { DashboardResponse, SystemHealth } from '../api/types'
import { StatusDot } from '../components/status/StatusDot'

const componentNames = ['api', 'simulator', 'sse', 'snapshot', 'database', 'forecast', 'planner', 'tripwire', 'execution'] as const

export function UntrustedBanner({ data }: { data: DashboardResponse }) {
  const freshness = data.state?.meta.freshness ?? 'UNAVAILABLE'
  if ((freshness === 'FRESH' || freshness === 'FIXTURE') && data.tripwire.state !== 'TRIPPED') return null
  const age = data.last_trusted_age_seconds
  return <section className="untrusted-banner" role="alert">
    <strong>SIMULATOR DATA UNTRUSTED</strong>
    <span>Last trusted tick {data.last_trusted_tick ?? 'none'} · age {age == null ? 'unknown' : Math.round(age) + 's'} · automatic execution paused</span>
  </section>
}

export function HealthPanel({ health }: { health: SystemHealth }) {
  return <section className="panel" aria-labelledby="health-title">
    <div className="panel-heading"><div><span className="eyebrow">SYSTEM</span><h2 id="health-title">Component health</h2></div><StatusDot status={health.status} /></div>
    <div className="health-list">{componentNames.map((name) => {
      const component = health.components[name]
      return <div className="health-row" key={name}><span className="capitalize">{name}</span><span title={component?.detail ?? undefined}><StatusDot status={component?.status ?? 'UNKNOWN'} /></span></div>
    })}</div>
  </section>
}
