import { useEffect, useRef, useCallback } from 'react'
import { WS_BASE_URL } from '../api/client'
import { useAppStore } from '../store'
import type { TelemetryPoint } from '../types'

const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

export function useTelemetryWS(satelliteId: string | null): void {
  const pushTelemetryPoint = useAppStore((s) => s.pushTelemetryPoint)
  const wsRef = useRef<WebSocket | null>(null)
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  const connect = useCallback(() => {
    if (!satelliteId || !mountedRef.current) return

    const token = useAppStore.getState().token
    if (!token) {
      retryTimerRef.current = setTimeout(connect, 2000)
      return
    }

    const url = `${WS_BASE_URL}/ws/telemetry/${satelliteId}?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      retryCountRef.current = 0
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const point = JSON.parse(event.data as string) as TelemetryPoint
        pushTelemetryPoint(point)
      } catch {
        console.warn('[TelemetryWS] Failed to parse message', event.data)
      }
    }

    ws.onerror = () => {
      // onerror is always followed by onclose; let onclose handle reconnect
    }

    ws.onclose = (event: CloseEvent) => {
      if (!mountedRef.current) return
      if (event.code === 1008) {
        console.warn('[TelemetryWS] Auth rejected (1008):', event.reason)
        useAppStore.getState().clearAuth()
        return
      }
      const delay = Math.min(
        BASE_DELAY_MS * Math.pow(2, retryCountRef.current),
        MAX_DELAY_MS,
      )
      retryCountRef.current += 1
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [satelliteId, pushTelemetryPoint])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current)
      }
      wsRef.current?.close()
    }
  }, [connect])
}
