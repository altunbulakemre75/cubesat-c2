import { useEffect, useRef, useCallback } from 'react'
import { WS_BASE_URL } from '../api/client'
import { useAppStore } from '../store'
import type { AppEvent } from '../types'

const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

export function useEventsWS(): void {
  const pushEvent = useAppStore((s) => s.pushEvent)
  const wsRef = useRef<WebSocket | null>(null)
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  const connect = useCallback(() => {
    if (!mountedRef.current) return

    const url = `${WS_BASE_URL}/ws/events`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      retryCountRef.current = 0
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const appEvent = JSON.parse(event.data as string) as AppEvent
        pushEvent(appEvent)
      } catch {
        console.warn('[EventsWS] Failed to parse message', event.data)
      }
    }

    ws.onerror = () => {
      // onerror always precedes onclose
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      const delay = Math.min(
        BASE_DELAY_MS * Math.pow(2, retryCountRef.current),
        MAX_DELAY_MS,
      )
      retryCountRef.current += 1
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [pushEvent])

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
