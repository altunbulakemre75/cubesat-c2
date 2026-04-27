import { create } from 'zustand'
import type { AppEvent, SatelliteListItem, TelemetryPoint } from '../types'

const MAX_EVENTS = 100
const MAX_TELEMETRY_POINTS = 100

interface AppState {
  // Auth (in-memory only — no localStorage)
  token: string | null
  refreshToken: string | null
  username: string | null
  setAuth: (token: string, username: string) => void
  setRefreshToken: (rt: string) => void
  clearAuth: () => void

  // Active satellite selection
  activeSatelliteId: string | null
  setActiveSatelliteId: (id: string | null) => void

  // Satellite list cache (from REST)
  satellites: SatelliteListItem[]
  setSatellites: (satellites: SatelliteListItem[]) => void

  // Live event feed (from WS /ws/events)
  events: AppEvent[]
  pushEvent: (event: AppEvent) => void
  clearEvents: () => void

  // Per-satellite live telemetry rolling windows
  telemetryWindows: Record<string, TelemetryPoint[]>
  pushTelemetryPoint: (point: TelemetryPoint) => void
  initTelemetryWindow: (satelliteId: string, points: TelemetryPoint[]) => void

  // Active alerts (critical/warning events not yet dismissed)
  activeAlerts: AppEvent[]
  dismissAlert: (id: string) => void
}

export const useAppStore = create<AppState>((set) => ({
  token: null,
  refreshToken: null,
  username: null,
  setAuth: (token, username) => set({ token, username }),
  setRefreshToken: (rt) => set({ refreshToken: rt }),
  clearAuth: () => set({ token: null, refreshToken: null, username: null }),

  activeSatelliteId: null,
  setActiveSatelliteId: (id) => set({ activeSatelliteId: id }),

  satellites: [],
  setSatellites: (satellites) => set({ satellites }),

  events: [],
  pushEvent: (event) =>
    set((state) => {
      const events = [event, ...state.events].slice(0, MAX_EVENTS)
      const activeAlerts =
        event.severity === 'warning' || event.severity === 'critical'
          ? [event, ...state.activeAlerts].slice(0, 20)
          : state.activeAlerts
      return { events, activeAlerts }
    }),
  clearEvents: () => set({ events: [] }),

  telemetryWindows: {},
  pushTelemetryPoint: (point) =>
    set((state) => {
      const existing = state.telemetryWindows[point.satellite_id] ?? []
      const updated = [...existing, point].slice(-MAX_TELEMETRY_POINTS)
      return {
        telemetryWindows: {
          ...state.telemetryWindows,
          [point.satellite_id]: updated,
        },
      }
    }),
  initTelemetryWindow: (satelliteId, points) =>
    set((state) => ({
      telemetryWindows: {
        ...state.telemetryWindows,
        [satelliteId]: points.slice(-MAX_TELEMETRY_POINTS),
      },
    })),

  activeAlerts: [],
  dismissAlert: (id) =>
    set((state) => ({
      activeAlerts: state.activeAlerts.filter((a) => a.id !== id),
    })),
}))
