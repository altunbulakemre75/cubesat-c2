import { useEffect, useRef } from 'react'
import { apiClient, WS_BASE_URL } from '../api/client'
import { useAppStore } from '../store'

const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000
const NO_TOKEN_POLL_MS = 2_000

// Close codes sent by the backend (see backend/src/api/ws.py).
const CLOSE_POLICY_VIOLATION = 1008 // credentials refused — retrying can't help
const CLOSE_SESSION_EXPIRED = 4001 // get a new ticket and reconnect right away

/**
 * Keeps a WebSocket open to `path` (e.g. "/ws/events") with reconnects.
 *
 * Each connection is opened with a single-use ticket from POST
 * /auth/ws-ticket instead of the access token: WebSocket URLs end up in
 * server access logs, and a 30-second single-use ticket is worthless there.
 * Fetching the ticket goes through apiClient, so an expired access token is
 * refreshed transparently first.
 */
export function useTicketedSocket<T>(path: string | null, onMessage: (data: T) => void): void {
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!path) return

    let socket: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let active = true

    const schedule = (delay: number) => {
      if (active) timer = setTimeout(() => void connect(), delay)
    }
    const backoff = () => {
      schedule(Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS))
      attempt += 1
    }

    async function connect(): Promise<void> {
      if (!active) return
      if (!useAppStore.getState().token) {
        schedule(NO_TOKEN_POLL_MS)
        return
      }

      let ticket: string
      try {
        const res = await apiClient.post<{ ticket: string }>('/auth/ws-ticket')
        ticket = res.data.ticket
      } catch {
        backoff()
        return
      }
      if (!active) return

      const ws = new WebSocket(`${WS_BASE_URL}${path}?ticket=${encodeURIComponent(ticket)}`)
      socket = ws

      ws.onopen = () => {
        attempt = 0
      }
      ws.onmessage = (event: MessageEvent) => {
        try {
          onMessageRef.current(JSON.parse(event.data as string) as T)
        } catch {
          console.warn(`[WS ${path}] Failed to parse message`, event.data)
        }
      }
      ws.onerror = () => {
        // onerror is always followed by onclose; reconnect is handled there
      }
      ws.onclose = (event: CloseEvent) => {
        if (!active) return
        if (event.code === CLOSE_POLICY_VIOLATION) {
          console.warn(`[WS ${path}] Access refused (1008):`, event.reason)
          return
        }
        if (event.code === CLOSE_SESSION_EXPIRED) {
          attempt = 0
          schedule(0)
          return
        }
        backoff()
      }
    }

    void connect()

    return () => {
      active = false
      if (timer !== null) clearTimeout(timer)
      socket?.close()
    }
  }, [path])
}
