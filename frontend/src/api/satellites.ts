import { apiClient } from './client'
import type { SatelliteListItem, SatelliteDetail } from '../types'

export async function fetchSatellites(): Promise<SatelliteListItem[]> {
  const response = await apiClient.get<SatelliteListItem[]>('/satellites')
  return response.data
}

export async function fetchSatellite(id: string): Promise<SatelliteDetail> {
  const response = await apiClient.get<SatelliteDetail>(`/satellites/${id}`)
  return response.data
}
