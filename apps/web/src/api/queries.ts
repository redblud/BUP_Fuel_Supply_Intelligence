import { useQuery } from '@tanstack/react-query'
import { api } from './client'

export const POLL_MS = 2000

export function useDashboard() {
  return useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, refetchInterval: POLL_MS })
}
