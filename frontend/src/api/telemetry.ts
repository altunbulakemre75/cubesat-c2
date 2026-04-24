import { apiClient } from './client'
import type { TelemetryPoint } from '../types'

export interface TelemetryQueryParams {
  limit?: number
  from?: string
  to?: string
}

export async function fetchTelemetry(
  satelliteId: string,
  params?: TelemetryQueryParams,
): Promise<TelemetryPoint[]> {
  const response = await apiClient.get<TelemetryPoint[]>(
    `/satellites/${satelliteId}/telemetry`,
    { params },
  )
  return response.data
}
