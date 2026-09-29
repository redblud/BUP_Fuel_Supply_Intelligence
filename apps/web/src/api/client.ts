import type { AutomationState, DashboardResponse, RecommendationState, SystemHealth, TrackedAllocation } from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body?.detail ?? {}
    throw new ApiError(res.status, detail.code ?? 'HTTP_ERROR', detail.message ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<SystemHealth>('/api/health'),
  dashboard: () => request<DashboardResponse>('/api/dashboard'),
  decisions: () => request<RecommendationState[]>('/api/decisions'),
  allocations: () => request<TrackedAllocation[]>('/api/allocations'),
  setAutomation: (body: AutomationState) =>
    request<AutomationState>('/api/automation', { method: 'PUT', body: JSON.stringify(body) }),
  recommendationAction: (id: string, action: 'approve' | 'reject') =>
    request<RecommendationState>('/api/recommendations/' + encodeURIComponent(id) + '/' + action, { method: 'POST' }),
  simControl: (action: 'run' | 'pause' | 'step') =>
    request<Record<string, unknown>>('/api/sim/control', { method: 'POST', body: JSON.stringify({ action }) }),
}
