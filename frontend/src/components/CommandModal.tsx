import { useState, useCallback } from 'react'
import { clsx } from 'clsx'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { sendCommand } from '../api/commands'
import { SAFE_MODE_ALLOWED_COMMANDS } from '../types'
import type { SatelliteMode } from '../types'

const COMMAND_TYPES = [
  'ping',
  'telemetry_request',
  'reboot',
  'mode_change',
  'antenna_deploy',
  'payload_on',
  'payload_off',
  'recovery',
  'diagnostic',
  'attitude_control',
  'downlink_schedule',
] as const

type CommandType = (typeof COMMAND_TYPES)[number]

interface CommandModalProps {
  satelliteId: string
  satelliteMode: SatelliteMode | null
  onClose: () => void
}

function isDisabledInSafeMode(commandType: string, mode: SatelliteMode | null): boolean {
  if (mode !== 'safe') return false
  return !(SAFE_MODE_ALLOWED_COMMANDS as readonly string[]).includes(commandType)
}

function parseParamsJson(raw: string): Record<string, unknown> | null {
  if (!raw.trim()) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
    return null
  } catch {
    return null
  }
}

export function CommandModal({ satelliteId, satelliteMode, onClose }: CommandModalProps) {
  const [commandType, setCommandType] = useState<CommandType>('ping')
  const [paramsRaw, setParamsRaw] = useState('')
  const [paramsError, setParamsError] = useState<string | null>(null)

  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: sendCommand,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['commands', satelliteId] })
      onClose()
    },
  })

  const disabled = isDisabledInSafeMode(commandType, satelliteMode)

  const handleParamsChange = useCallback((value: string) => {
    setParamsRaw(value)
    if (!value.trim()) {
      setParamsError(null)
      return
    }
    const parsed = parseParamsJson(value)
    setParamsError(parsed === null ? 'Invalid JSON object' : null)
  }, [])

  const handleSubmit = () => {
    if (disabled || mutation.isPending) return

    const params = parseParamsJson(paramsRaw)
    if (params === null) {
      setParamsError('Invalid JSON object')
      return
    }

    mutation.mutate({
      satellite_id: satelliteId,
      command_type: commandType,
      params: Object.keys(params).length > 0 ? params : undefined,
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-full max-w-md rounded-xl border border-space-border bg-space-panel shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-space-border p-4">
          <div>
            <h2 className="font-mono text-sm font-semibold text-white">Send Command</h2>
            <p className="font-mono text-xs text-gray-500">
              Satellite: <span className="text-gray-300">{satelliteId}</span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-gray-500 hover:bg-gray-800 hover:text-white"
          >
            ✕
          </button>
        </div>

        {/* Safe mode warning */}
        {satelliteMode === 'safe' && (
          <div className="mx-4 mt-4 rounded border border-red-700 bg-red-900/20 p-3">
            <p className="font-mono text-xs font-semibold text-red-400">
              SAFE MODE ACTIVE
            </p>
            <p className="mt-0.5 font-mono text-xs text-red-300">
              Only <code className="rounded bg-red-900/50 px-1">recovery</code>,{' '}
              <code className="rounded bg-red-900/50 px-1">mode_change</code>, and{' '}
              <code className="rounded bg-red-900/50 px-1">diagnostic</code> commands are
              permitted.
            </p>
          </div>
        )}

        {/* Form */}
        <div className="space-y-4 p-4">
          {/* Command type */}
          <div>
            <label className="mb-1 block font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
              Command Type
            </label>
            <select
              value={commandType}
              onChange={(e) => setCommandType(e.target.value as CommandType)}
              className="w-full rounded border border-space-border bg-space-dark px-3 py-2 font-mono text-sm text-white outline-none focus:border-space-accent"
            >
              {COMMAND_TYPES.map((ct) => {
                const isLocked = isDisabledInSafeMode(ct, satelliteMode)
                return (
                  <option key={ct} value={ct} disabled={isLocked}>
                    {ct}
                    {isLocked ? ' (locked in safe mode)' : ''}
                  </option>
                )
              })}
            </select>
          </div>

          {/* Params JSON */}
          <div>
            <label className="mb-1 block font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
              Parameters (JSON, optional)
            </label>
            <textarea
              value={paramsRaw}
              onChange={(e) => handleParamsChange(e.target.value)}
              placeholder='{"key": "value"}'
              rows={4}
              className={clsx(
                'w-full rounded border bg-space-dark px-3 py-2 font-mono text-sm text-white outline-none',
                'placeholder-gray-600 focus:border-space-accent',
                paramsError ? 'border-red-500' : 'border-space-border',
              )}
            />
            {paramsError && (
              <p className="mt-1 font-mono text-xs text-red-400">{paramsError}</p>
            )}
          </div>

          {/* Error from mutation */}
          {mutation.isError && (
            <div className="rounded border border-red-700 bg-red-900/20 p-2">
              <p className="font-mono text-xs text-red-400">
                Failed to send command. Check backend connectivity.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-space-border p-4">
          <button
            onClick={onClose}
            className="rounded px-4 py-2 font-mono text-sm text-gray-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={disabled || mutation.isPending || paramsError !== null}
            className={clsx(
              'rounded px-4 py-2 font-mono text-sm font-semibold transition-colors',
              disabled || paramsError !== null
                ? 'cursor-not-allowed bg-gray-700 text-gray-500'
                : 'bg-space-accent text-white hover:bg-blue-500',
              mutation.isPending && 'opacity-70',
            )}
          >
            {mutation.isPending ? 'Sending…' : 'Send Command'}
          </button>
        </div>
      </div>
    </div>
  )
}
