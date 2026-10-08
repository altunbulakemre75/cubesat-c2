import { apiClient } from './client'
import type { Command } from '../types'

export interface SendCommandPayload {
  satellite_id: string
  command_type: string
  params?: Record<string, unknown>
  // Set after the server answered 409: the satellite's mode is unknown or
  // stale, and the operator chose to queue the command anyway.
  confirm_unverified_mode?: boolean
}

export async function fetchCommands(satelliteId?: string): Promise<Command[]> {
  const response = await apiClient.get<Command[]>('/commands', {
    params: satelliteId ? { satellite_id: satelliteId } : undefined,
  })
  return response.data
}

export async function sendCommand(payload: SendCommandPayload): Promise<Command> {
  const response = await apiClient.post<Command>('/commands', payload)
  return response.data
}

// Critical commands (separation, factory_reset) wait in 'awaiting_approval'
// until a different admin approves them; the server enforces both rules.
export async function approveCommand(id: string): Promise<Command> {
  const response = await apiClient.post<Command>(`/commands/${id}/approve`)
  return response.data
}

export async function cancelCommand(id: string): Promise<void> {
  await apiClient.delete(`/commands/${id}`)
}
