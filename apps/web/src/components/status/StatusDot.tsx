const COLORS: Record<string, string> = {
  HEALTHY: '#16a34a',
  FRESH: '#16a34a',
  CLEAR: '#16a34a',
  FIXTURE: '#2563eb',
  UNKNOWN: '#9ca3af',
  DEGRADED: '#d97706',
  STALE: '#d97706',
  TORN: '#d97706',
  RESET_UNCERTAIN: '#d97706',
  DOWN: '#dc2626',
  UNAVAILABLE: '#dc2626',
  TRIPPED: '#dc2626',
}

export function StatusDot({ status }: { status: string }) {
  return (
    <span className="status">
      <span className="dot" style={{ background: COLORS[status] ?? '#9ca3af' }} />
      {status}
    </span>
  )
}
