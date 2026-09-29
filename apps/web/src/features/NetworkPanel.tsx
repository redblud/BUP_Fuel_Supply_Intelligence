import type { NetworkState } from '../api/types'
import { liters, title } from '../lib/format'

const fuels = ['DIESEL', 'PETROL', 'OCTANE'] as const

function Inventory({ inventory, capacity }: { inventory: NetworkState['stations'][number]['inventory']; capacity: NetworkState['stations'][number]['capacity'] }) {
  return <div className="inventory-list">{fuels.map((fuel) => {
    const amount = inventory[fuel] ?? 0
    const max = capacity[fuel] ?? 0
    const fraction = max > 0 ? Math.max(0, Math.min(100, amount / max * 100)) : 0
    return <div className="inventory-row" key={fuel}><span>{fuel}</span><div className="inventory-track" role="meter" aria-label={fuel + ' inventory'} aria-valuemin={0} aria-valuemax={max} aria-valuenow={amount}><i style={{ width: fraction + '%' }} /></div><strong>{liters(amount)}</strong></div>
  })}</div>
}

export function NetworkPanel({ state }: { state: NetworkState | null }) {
  return <section className="panel" aria-labelledby="network-title">
    <div className="panel-heading"><div><span className="eyebrow">LIVE NETWORK</span><h2 id="network-title">Depots and stations</h2></div><span className="count-pill">{state ? state.depots.length + ' depots · ' + state.stations.length + ' stations' : 'Unavailable'}</span></div>
    {!state ? <p className="muted">Network state unavailable.</p> : <div className="network-list">
      {state.depots.map((depot) => {
        const routes = state.routes.filter((route) => route.source_depot_id === depot.id)
        return <div className="depot-group" key={depot.id}>
          <div className="entity-heading"><div><strong>{depot.name}</strong><small>Dispatch capacity {liters(depot.dispatch_capacity_per_tick)}/tick</small></div><span className={'entity-status ' + (depot.status === 'CONSTRAINED' ? 'bad' : 'good')}>{depot.status}</span></div>
          <Inventory inventory={depot.inventory} capacity={depot.capacity} />
          <div className="route-list">{routes.length === 0 ? <p className="muted">No routes from this depot.</p> : routes.map((route) => {
            const station = state.stations.find((item) => item.id === route.destination_station_id)
            return <div className={'route-item ' + (route.status === 'DISRUPTED' || station?.status === 'OUTAGE' ? 'route-warning' : '')} key={route.id}>
              <span className="route-connector">↳</span><div className="route-content"><div className="route-heading"><strong>{station?.name ?? title(route.destination_station_id)}</strong><span className={'entity-status ' + (station?.status === 'OUTAGE' ? 'bad' : 'good')}>{station?.status ?? 'UNKNOWN'}</span></div>
                <small>{title(route.id)} · {route.transit_ticks} ticks · {route.status}</small>
                {station ? <Inventory inventory={station.inventory} capacity={station.capacity} /> : null}
              </div>
            </div>
          })}</div>
        </div>
      })}
    </div>}
  </section>
}
