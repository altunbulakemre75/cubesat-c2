import { describe, it, expect, afterEach } from 'vitest'
import type { AxiosAdapter, AxiosResponse } from 'axios'
import { apiClient } from './client'
import { fetchTelemetry } from './telemetry'

const originalAdapter = apiClient.defaults.adapter

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter
})

function point(seq: number, ts: string) {
  return {
    timestamp: ts, satellite_id: 'SAT1', sequence: seq,
    params: {
      battery_voltage_v: 3.9, temperature_obcs_c: 20, temperature_eps_c: 18,
      solar_power_w: 1, rssi_dbm: -90, uptime_s: seq, mode: 'nominal',
    },
  }
}

describe('fetchTelemetry', () => {
  it('asks the backend route that exists and returns oldest first', async () => {
    // The page called /satellites/{id}/telemetry, which doesn't exist (404):
    // charts started empty until live points arrived.
    let seen: { url?: string; params?: unknown } = {}
    const adapter: AxiosAdapter = async (config) => {
      seen = { url: config.url, params: config.params }
      const response: AxiosResponse = {
        // the API returns newest first
        data: [point(3, '2026-10-08T10:00:03Z'), point(2, '2026-10-08T10:00:02Z'),
               point(1, '2026-10-08T10:00:01Z')],
        status: 200, statusText: 'OK', headers: {}, config,
      }
      return response
    }
    apiClient.defaults.adapter = adapter

    const pts = await fetchTelemetry('SAT1', { limit: 100, from: 'A', to: 'B' })

    expect(seen.url).toBe('/telemetry/SAT1')
    expect(seen.params).toEqual({ limit: 100, from_time: 'A', to_time: 'B' })
    expect(pts.map((p) => p.sequence)).toEqual([1, 2, 3])
  })
})
