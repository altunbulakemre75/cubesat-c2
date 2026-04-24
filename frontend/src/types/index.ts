export type SatelliteMode = 'beacon' | 'deployment' | 'nominal' | 'science' | 'safe'

export interface SatelliteListItem {
  id: string
  name: string
  mode: SatelliteMode
  last_seen: string // ISO datetime
  battery_voltage_v: number
  norad_id?: number
}

export interface SatelliteDetail extends SatelliteListItem {
  tle_line1?: string
  tle_line2?: string
  description?: string
  launch_date?: string
}

export interface TelemetryParams {
  battery_voltage_v: number
  temperature_obcs_c: number
  temperature_eps_c: number
  solar_power_w: number
  rssi_dbm: number
  uptime_s: number
  mode: SatelliteMode
}

export interface TelemetryPoint {
  timestamp: string
  satellite_id: string
  sequence: number
  params: TelemetryParams
}

export interface Pass {
  satellite_id: string
  station_id: number
  station_name: string
  aos: string // ISO datetime
  los: string
  max_elevation_deg: number
  azimuth_at_aos_deg: number
}

export interface Command {
  id: string // UUID
  satellite_id: string
  command_type: string
  status:
    | 'pending'
    | 'scheduled'
    | 'transmitting'
    | 'sent'
    | 'acked'
    | 'timeout'
    | 'retry'
    | 'dead'
  created_at: string
  params?: Record<string, unknown>
}

export interface Anomaly {
  id: string
  satellite_id: string
  parameter: string
  value: number
  z_score: number
  severity: 'warning' | 'critical'
  detected_at: string
}

export interface AppEvent {
  id: string
  type: 'fdir_alert' | 'mode_change' | 'anomaly' | 'command_ack' | 'info'
  satellite_id?: string
  message: string
  timestamp: string
  severity?: 'info' | 'warning' | 'critical'
}

export type CommandStatus = Command['status']

export const SAFE_MODE_ALLOWED_COMMANDS = ['recovery', 'mode_change', 'diagnostic'] as const
export type SafeModeAllowedCommand = (typeof SAFE_MODE_ALLOWED_COMMANDS)[number]
