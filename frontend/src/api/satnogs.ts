import { apiClient } from './client'

export interface SatnogsObservation {
  id: number
  satellite_id: string | null
  norad_cat_id: number
  observer: string | null
  transmitter: string | null
  timestamp_utc: string
  frame_hex: string | null
  decoded_json: Record<string, unknown> | null
  app_source: string | null
}

export async function fetchSatnogsObservations(opts: {
  satellite_id?: string
  norad_id?: number
  limit?: number
} = {}): Promise<SatnogsObservation[]> {
  const params: Record<string, string> = {}
  if (opts.satellite_id) params.satellite_id = opts.satellite_id
  if (opts.norad_id) params.norad_id = String(opts.norad_id)
  if (opts.limit) params.limit = String(opts.limit)
  const response = await apiClient.get<SatnogsObservation[]>('/satnogs/observations', { params })
  return response.data
}
