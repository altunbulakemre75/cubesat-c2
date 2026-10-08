import { apiClient } from './client'
import type { TelemetryPoint } from '../types'

export interface TelemetryQueryParams {
  limit?: number
  from?: string
  to?: string
}

// History for the charts, oldest first (the API returns newest first).
export async function fetchTelemetry(
  satelliteId: string,
  params?: TelemetryQueryParams,
): Promise<TelemetryPoint[]> {
  const response = await apiClient.get<TelemetryPoint[]>(
    `/telemetry/${encodeURIComponent(satelliteId)}`,
    {
      params: {
        limit: params?.limit,
        from_time: params?.from,
        to_time: params?.to,
      },
    },
  )
  return [...response.data].reverse()
}
