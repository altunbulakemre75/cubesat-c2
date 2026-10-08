import type { Pass } from '../types'

export interface PassKind {
  label: 'UPLINK' | 'RX'
  description: string
}

// The scheduler only puts commands on passes over uplink-capable stations;
// receive-only stations (e.g. the SatNOGS network) still show up here.
export function passKind(pass: Pass): PassKind {
  return pass.uplink_capable
    ? { label: 'UPLINK', description: 'Uplink window — queued commands can be sent' }
    : { label: 'RX', description: 'Receive only — commands are not sent on this pass' }
}
