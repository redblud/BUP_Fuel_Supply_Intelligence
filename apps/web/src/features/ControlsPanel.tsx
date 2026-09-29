import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import type { AutomationState, DashboardResponse } from '../api/types'
import { title } from '../lib/format'

export function ControlsPanel({ data }: { data: DashboardResponse }) {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState('')
  const [mode, setMode] = useState<AutomationState['mode']>(data.automation.mode)
  const blocked = data.tripwire.state === 'TRIPPED'
  const automation = useMutation({ mutationFn: api.setAutomation, onSuccess: async () => { setMessage('Automation updated.'); await queryClient.invalidateQueries({ queryKey: ['dashboard'] }) }, onError: showError })
  const approval = useMutation({ mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' }) => api.recommendationAction(id, action), onSuccess: async (result) => { setMessage('Recommendation ' + result.status.toLowerCase() + '.'); await queryClient.invalidateQueries({ queryKey: ['dashboard'] }) }, onError: showError })
  const simulator = useMutation({ mutationFn: api.simControl, onSuccess: async () => { setMessage('Simulator command sent.'); await queryClient.invalidateQueries({ queryKey: ['dashboard'] }) }, onError: showError })

  function showError(error: Error) {
    if (error instanceof ApiError && error.status === 501) setMessage('This control is not available from the current backend.')
    else if (error instanceof ApiError && error.status === 409) setMessage('State changed. Refresh and review before trying again: ' + error.message)
    else setMessage(error.message)
  }

  return <section className="panel" aria-labelledby="controls-title">
    <div className="panel-heading"><div><span className="eyebrow">OPERATOR ACTIONS</span><h2 id="controls-title">Controls</h2></div></div>
    <div className="control-group"><label className="field-label" htmlFor="mode-select">Operating mode</label><div className="control-inline"><select id="mode-select" value={mode} onChange={(event) => setMode(event.target.value as AutomationState['mode'])} disabled={blocked || automation.isPending}><option value="ADVISORY">Advisory</option><option value="GUARDED_AUTO">Guarded auto</option><option value="MANUAL_DEMO">Manual demo</option></select><button className="control-button" disabled={blocked || automation.isPending} onClick={() => automation.mutate({ mode, kill_switch: data.automation.kill_switch })}>Apply</button></div></div>
    <div className="control-group"><span className="field-label">Kill switch</span><div className="control-inline"><strong className={data.automation.kill_switch ? 'danger-text' : ''}>{data.automation.kill_switch ? 'ON' : 'OFF'}</strong><button className="control-button" disabled={blocked || automation.isPending} onClick={() => automation.mutate({ mode: data.automation.mode, kill_switch: !data.automation.kill_switch })}>{data.automation.kill_switch ? 'Turn off' : 'Turn on'}</button></div></div>
    {blocked ? <p className="danger-text">Tripwire tripped. Automation controls are paused.</p> : null}
    <div className="control-group"><span className="field-label">Simulator demo</span><div className="control-inline">{(['run', 'pause', 'step'] as const).map((action) => <button className="control-button" key={action} disabled={simulator.isPending} onClick={() => simulator.mutate(action)}>{title(action)}</button>)}</div></div>
    {data.plan && data.plan.recommendations.length > 0 ? <div className="control-group"><span className="field-label">Recommendation decisions</span>{data.plan.recommendations.map((rec) => <div className="decision-control" key={rec.id}><span>{title(rec.station_id)} · {rec.fuel_type}</span><div className="control-inline"><button className="control-button" disabled={blocked || approval.isPending || data.state?.meta.freshness === 'STALE'} onClick={() => approval.mutate({ id: rec.id, action: 'approve' })}>Approve</button><button className="control-button secondary" disabled={approval.isPending} onClick={() => approval.mutate({ id: rec.id, action: 'reject' })}>Reject</button></div></div>)}</div> : null}
    {message ? <p className="control-message" role="status">{message}</p> : null}
  </section>
}
