import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AxiosAdapter, AxiosResponse } from 'axios'
import { AxiosError } from 'axios'
import { apiClient } from '../api/client'
import { CommandModal } from './CommandModal'

const originalAdapter = apiClient.defaults.adapter

function respond(sequence: Array<{ status: number; data: unknown }>): unknown[] {
  const bodies: unknown[] = []
  const adapter: AxiosAdapter = async (config) => {
    bodies.push(JSON.parse(config.data as string))
    const { status, data } = sequence[Math.min(bodies.length - 1, sequence.length - 1)]
    const response: AxiosResponse = { data, status, statusText: '', headers: {}, config }
    if (status >= 400) throw new AxiosError('fail', String(status), config, null, response)
    return response
  }
  apiClient.defaults.adapter = adapter
  return bodies
}

function renderModal(onClose = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CommandModal satelliteId="SAT1" satelliteMode={null} onClose={onClose} />
    </QueryClientProvider>,
  )
  return onClose
}

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter
})

describe('CommandModal — unverifiable satellite mode', () => {
  it('asks for confirmation and re-sends with confirm_unverified_mode', async () => {
    const detail = "Satellite mode can't be verified (no mode reported yet)."
    const bodies = respond([
      { status: 409, data: { detail } },
      { status: 201, data: { id: 'c1' } },
    ])
    const onClose = renderModal()

    fireEvent.click(screen.getByRole('button', { name: /send command/i }))
    expect(await screen.findByText(detail)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /queue anyway/i }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(bodies[0]).not.toHaveProperty('confirm_unverified_mode')
    expect(bodies[1]).toMatchObject({ confirm_unverified_mode: true })
  })

  it('shows the policy reason when the server refuses the command', async () => {
    const reason = "Command 'camera_on' is not permitted when satellite is in 'safe' mode."
    respond([{ status: 422, data: { detail: reason } }])
    renderModal()

    fireEvent.click(screen.getByRole('button', { name: /send command/i }))

    expect(await screen.findByText(reason)).toBeInTheDocument()
  })
})
