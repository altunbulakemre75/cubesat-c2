import { apiClient } from './client'
import type { Pass, Anomaly } from '../types'

export async function fetchPasses(satelliteId: string): Promise<Pass[]> {
  const response = await apiClient.get<Pass[]>(`/satellites/${satelliteId}/passes`)
  return response.data
}

export async function fetchAnomalies(satelliteId: string): Promise<Anomaly[]> {
  const response = await apiClient.get<Anomaly[]>('/anomalies', {
    params: { satellite_id: satelliteId },
  })
  return response.data
}
