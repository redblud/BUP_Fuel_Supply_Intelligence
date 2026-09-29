import { useState } from 'react'
import { useDashboard } from '../api/queries'
import { StatusDot } from '../components/status/StatusDot'
import { HealthPanel, UntrustedBanner } from '../features/HealthPanel'
import { RiskPanel } from '../features/RiskPanel'
import { RecommendationPanel } from '../features/RecommendationPanel'
import { NetworkPanel } from '../features/NetworkPanel'
import { ProjectionPanel } from '../features/ProjectionPanel'
import { EventsPanel } from '../features/EventsPanel'
import { HistoryPanel } from '../features/HistoryPanel'
import { ControlsPanel } from '../features/ControlsPanel'
import { title } from '../lib/format'

export function OperationsDashboard() {
  const { data, error, isPending, isFetching, refetch } = useDashboard()
  const [selectedRisk, setSelectedRisk] = useState('')

  if (isPending) {
    return <main className="app-shell"><div className="loading-state"><span className="pulse-mark" />Connecting to FuelOps…</div></main>
  }

  if (error || !data) {
    return <main className="app-shell"><header className="topbar"><h1>FuelOps <strong>AI</strong></h1><span className="simulation-badge">SIMULATION</span></header><section className="panel empty-state" role="alert"><h2>Dashboard unavailable</h2><p>{error?.message ?? 'The API did not return dashboard data.'}</p><button onClick={() => void refetch()}>Try again</button></section></main>
  }

  const { state, plan, tripwire, health, automation } = data
  const freshness = state?.meta.freshness ?? 'UNAVAILABLE'
  const trusted = freshness === 'FRESH' || freshness === 'FIXTURE'
  const risks = plan?.risks ?? []
  const recommendations = plan?.recommendations ?? []

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">F</span><div><h1>FuelOps <strong>AI</strong></h1><p>Operator console</p></div></div>
        <div className="topbar-right"><span className="simulation-badge">SIMULATION</span><span className="refresh-label" aria-live="polite">{isFetching ? 'Updating…' : 'Updates every 2s'}</span></div>
      </header>

      <section className="overview" aria-label="System overview">
        <div className="overview-item"><span>Current tick</span><strong>{state?.run.tick ?? '—'}</strong></div>
        <div className="overview-item"><span>Simulation time</span><strong>{state ? new Date(state.run.sim_time).toLocaleString() : 'Unavailable'}</strong></div>
        <div className="overview-item"><span>Snapshot</span><strong><StatusDot status={freshness} /></strong></div>
        <div className="overview-item"><span>Mode</span><strong>{title(automation.mode)}</strong></div>
        <div className="overview-item"><span>Tripwire</span><strong><StatusDot status={tripwire.state} /></strong></div>
        <div className="overview-item"><span>Kill switch</span><strong className={automation.kill_switch ? 'danger-text' : ''}>{automation.kill_switch ? 'ON' : 'OFF'}</strong></div>
      </section>

      <UntrustedBanner data={data} />


      <div className="dashboard-grid">
        <div className="dashboard-column">
          <NetworkPanel state={state} />
          <EventsPanel state={state} risks={risks} trips={tripwire.trips} />
        </div>
        <div className="dashboard-column">
          <RiskPanel risks={risks} planAvailable={plan !== null} onSelect={setSelectedRisk} />
          <ProjectionPanel plan={plan} state={state} selectedKey={selectedRisk} onSelect={setSelectedRisk} />
          <HealthPanel health={health} />
        </div>
        <div className="dashboard-column">
          <RecommendationPanel recommendations={recommendations} states={data.recommendation_states} planAvailable={plan !== null} trusted={trusted} />
          <ControlsPanel data={data} />
          <HistoryPanel data={data} />
        </div>
      </div>
    </main>
  )
}


