import { apiClient } from './client'

export interface FDIRAlert {
  id: string
  satellite_id: string
  reason: string
  severity: 'critical' | 'warning'
  triggered_at: string
  acknowledged: boolean
  acknowledged_by: string | null
  acknowledged_at: string | null
}

export async function fetchFDIRAlerts(opts: {
  unacknowledged_only?: boolean
  satellite_id?: string
  limit?: number
} = {}): Promise<FDIRAlert[]> {
  const params: Record<string, string> = {}
  if (opts.unacknowledged_only) params.unacknowledged_only = 'true'
  if (opts.satellite_id) params.satellite_id = opts.satellite_id
  if (opts.limit) params.limit = String(opts.limit)
  const response = await apiClient.get<FDIRAlert[]>('/fdir/alerts', { params })
  return response.data
}

export async function ackFDIRAlert(alertId: string): Promise<FDIRAlert> {
  const response = await apiClient.post<FDIRAlert>(`/fdir/alerts/${alertId}/ack`)
  return response.data
}
