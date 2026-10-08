import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from './index'

describe('useAppStore — auth slice', () => {
  beforeEach(() => {
    useAppStore.getState().clearAuth()
  })

  it('starts with no auth', () => {
    const s = useAppStore.getState()
    expect(s.token).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.username).toBeNull()
  })

  it('setAuth records token and username', () => {
    useAppStore.getState().setAuth('access-xyz', 'alice')
    const s = useAppStore.getState()
    expect(s.token).toBe('access-xyz')
    expect(s.username).toBe('alice')
  })

  it('setRefreshToken stores refresh independently of access', () => {
    useAppStore.getState().setAuth('access-xyz', 'alice')
    useAppStore.getState().setRefreshToken('refresh-abc')
    const s = useAppStore.getState()
    expect(s.refreshToken).toBe('refresh-abc')
    expect(s.token).toBe('access-xyz')   // still there
  })

  it('clearAuth wipes all three fields', () => {
    useAppStore.getState().setAuth('a', 'b')
    useAppStore.getState().setRefreshToken('c')
    useAppStore.getState().clearAuth()
    const s = useAppStore.getState()
    expect(s.token).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.username).toBeNull()
  })
})

describe('useAppStore — telemetry history merge', () => {
  const pt = (seq: number, sec: number) => ({
    timestamp: `2026-10-08T10:00:${String(sec).padStart(2, '0')}Z`,
    satellite_id: 'SAT1', sequence: seq,
    params: {
      battery_voltage_v: 3.9, temperature_obcs_c: 20, temperature_eps_c: 18,
      solar_power_w: 1, rssi_dbm: -90, uptime_s: seq, mode: 'nominal' as const,
    },
  })

  it('keeps live points that arrived before the history and orders by time', () => {
    const store = useAppStore.getState()
    store.initTelemetryWindow('SAT1', [])
    store.pushTelemetryPoint(pt(5, 5))          // live point raced ahead
    store.mergeTelemetryHistory('SAT1', [pt(3, 3), pt(4, 4), pt(5, 5)])

    const seqs = useAppStore.getState().telemetryWindows['SAT1'].map((p) => p.sequence)
    expect(seqs).toEqual([3, 4, 5])
  })
})
