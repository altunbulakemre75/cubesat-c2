import { useAppStore } from '../store'
import type { TelemetryPoint } from '../types'
import { useTicketedSocket } from './useTicketedSocket'

export function useTelemetryWS(satelliteId: string | null): void {
  const pushTelemetryPoint = useAppStore((s) => s.pushTelemetryPoint)
  useTicketedSocket<TelemetryPoint>(
    satelliteId ? `/ws/telemetry/${encodeURIComponent(satelliteId)}` : null,
    pushTelemetryPoint,
  )
}
