import { apiClient } from './client'
import type { Command } from '../types'

export interface SendCommandPayload {
  satellite_id: string
  command_type: string
  params?: Record<string, unknown>
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
