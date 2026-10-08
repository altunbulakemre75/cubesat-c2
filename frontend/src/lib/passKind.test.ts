import { describe, it, expect } from 'vitest'
import { passKind } from './passKind'
import type { Pass } from '../types'

const base: Pass = {
  satellite_id: 'SAT1',
  station_id: 1,
  station_name: 'GS',
  aos: '2026-10-08T12:00:00Z',
  los: '2026-10-08T12:08:00Z',
  max_elevation_deg: 40,
  azimuth_at_aos_deg: 120,
  uplink_capable: false,
}

describe('passKind', () => {
  // Commands are only scheduled onto passes over stations that can
  // transmit; operators need to see which passes those are.
  it('labels passes over transmitting stations as uplink windows', () => {
    expect(passKind({ ...base, uplink_capable: true })).toEqual({
      label: 'UPLINK',
      description: 'Uplink window — queued commands can be sent',
    })
  })

  it('labels receive-only passes (e.g. SatNOGS stations)', () => {
    expect(passKind(base)).toEqual({
      label: 'RX',
      description: 'Receive only — commands are not sent on this pass',
    })
  })

  it('treats passes from an older backend without the field as receive-only', () => {
    const legacy = { ...base } as Partial<Pass>
    delete legacy.uplink_capable
    expect(passKind(legacy as Pass).label).toBe('RX')
  })
})
