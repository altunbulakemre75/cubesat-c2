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

export interface TLEData {
  satellite_id: string
  epoch: string
  tle_line1: string
  tle_line2: string
}

export async function fetchTLE(satelliteId: string): Promise<TLEData | null> {
  try {
    const response = await apiClient.get<TLEData>(`/satellites/${satelliteId}/tle`)
    return response.data
  } catch {
    return null
  }
}

export async function fetchAllTLEs(satelliteIds: string[]): Promise<TLEData[]> {
  const results = await Promise.all(satelliteIds.map(fetchTLE))
  return results.filter((t): t is TLEData => t !== null)
}
