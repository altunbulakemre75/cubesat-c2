import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { AxiosAdapter, AxiosResponse } from 'axios'
import { apiClient } from '../api/client'
import { useAppStore } from '../store'
import { useTicketedSocket } from './useTicketedSocket'

class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((e: CloseEvent) => void) | null = null
  closed = false

  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }

  close(): void {
    this.closed = true
  }

  serverClose(code: number): void {
    this.onclose?.({ code, reason: '' } as CloseEvent)
  }
}

const originalAdapter = apiClient.defaults.adapter
let ticketsIssued = 0

beforeEach(() => {
  FakeSocket.instances = []
  ticketsIssued = 0
  vi.stubGlobal('WebSocket', FakeSocket)
  useAppStore.getState().setAuth('secret-access-token', 'alice')
  const adapter: AxiosAdapter = async (config) => {
    ticketsIssued += 1
    const response: AxiosResponse = {
      data: { ticket: `ticket-${ticketsIssued}`, expires_in: 30 },
      status: 200, statusText: 'OK', headers: {}, config,
    }
    return response
  }
  apiClient.defaults.adapter = adapter
})

afterEach(() => {
  vi.unstubAllGlobals()
  apiClient.defaults.adapter = originalAdapter
  useAppStore.getState().clearAuth()
})

describe('useTicketedSocket', () => {
  it('connects with a one-time ticket, never the access token', async () => {
    renderHook(() => useTicketedSocket('/ws/events', () => {}))

    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const url = FakeSocket.instances[0].url
    expect(url).toContain('/ws/events?ticket=ticket-1')
    expect(url).not.toContain('secret-access-token')
  })

  it('reconnects with a fresh ticket when the session expires (4001)', async () => {
    renderHook(() => useTicketedSocket('/ws/events', () => {}))
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))

    FakeSocket.instances[0].serverClose(4001)

    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(2))
    expect(FakeSocket.instances[1].url).toContain('ticket=ticket-2')
  })

  it('stops without logging the user out when access is refused (1008)', async () => {
    // e.g. a viewer on the operator-only events stream: retrying can't
    // help, and the REST session is still perfectly valid.
    renderHook(() => useTicketedSocket('/ws/events', () => {}))
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))

    FakeSocket.instances[0].serverClose(1008)
    await new Promise((r) => setTimeout(r, 50))

    expect(FakeSocket.instances).toHaveLength(1)
    expect(useAppStore.getState().token).toBe('secret-access-token')
  })

  it('passes parsed messages to the handler', async () => {
    const onMessage = vi.fn()
    renderHook(() => useTicketedSocket('/ws/events', onMessage))
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))

    FakeSocket.instances[0].onmessage?.({ data: '{"id":"e1"}' } as MessageEvent)

    expect(onMessage).toHaveBeenCalledWith({ id: 'e1' })
  })

  it('closes the socket on unmount', async () => {
    const { unmount } = renderHook(() => useTicketedSocket('/ws/events', () => {}))
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))

    unmount()

    expect(FakeSocket.instances[0].closed).toBe(true)
  })
})
