import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { fetchSatellites } from '../api/satellites'
import { fetchCommands } from '../api/commands'
import { CommandModal } from '../components/CommandModal'
import { ModeBadge } from '../components/ModeBadge'
import { useAppStore } from '../store'
import type { Command } from '../types'

const COMMAND_STATUS_STYLES: Record<Command['status'], string> = {
  pending: 'bg-gray-700 text-gray-300 border-gray-600',
  scheduled: 'bg-blue-800 text-blue-200 border-blue-700',
  transmitting: 'bg-yellow-800 text-yellow-200 border-yellow-700',
  sent: 'bg-cyan-800 text-cyan-200 border-cyan-700',
  acked: 'bg-green-800 text-green-200 border-green-700',
  timeout: 'bg-orange-800 text-orange-200 border-orange-700',
  retry: 'bg-purple-800 text-purple-200 border-purple-700',
  dead: 'bg-red-900 text-red-300 border-red-700',
}

const STATUS_ORDER: Command['status'][] = [
  'pending',
  'scheduled',
  'transmitting',
  'sent',
  'acked',
  'timeout',
  'retry',
  'dead',
]

function formatIso(iso: string): string {
  return new Date(iso).toLocaleString()
}

interface CommandRowProps {
  command: Command
}

function CommandRow({ command }: CommandRowProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border border-space-border bg-space-dark">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between p-3 text-left"
      >
        <div className="flex items-center gap-3">
          <span
            className={clsx(
              'rounded border px-1.5 py-0.5 font-mono text-xs font-semibold uppercase',
              COMMAND_STATUS_STYLES[command.status],
            )}
          >
            {command.status}
          </span>
          <span className="font-mono text-sm text-white">{command.command_type}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-gray-500">
            {formatIso(command.created_at)}
          </span>
          <span className="text-gray-500">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-space-border px-3 pb-3 pt-2">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
            <div>
              <span className="text-gray-500">ID:</span>{' '}
              <span className="text-gray-300">{command.id}</span>
            </div>
            <div>
              <span className="text-gray-500">Satellite:</span>{' '}
              <span className="text-gray-300">{command.satellite_id}</span>
            </div>
          </div>
          {command.params && Object.keys(command.params).length > 0 && (
            <div className="mt-2">
              <p className="font-mono text-xs text-gray-500">Params:</p>
              <pre className="mt-1 overflow-x-auto rounded bg-space-panel p-2 font-mono text-xs text-gray-300">
                {JSON.stringify(command.params, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function CommandCenter() {
  const [selectedSatelliteId, setSelectedSatelliteId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<Command['status'] | 'all'>('all')
  const [showModal, setShowModal] = useState(false)

  const satellites = useAppStore((s) => s.satellites)

  const { data: fetchedSatellites, isLoading: loadingSats } = useQuery({
    queryKey: ['satellites'],
    queryFn: fetchSatellites,
    refetchInterval: 30_000,
  })

  // Use whichever source has data
  const satList = fetchedSatellites ?? satellites

  const { data: commands, isLoading: loadingCmds, isError } = useQuery({
    queryKey: ['commands', selectedSatelliteId],
    queryFn: () => fetchCommands(selectedSatelliteId ?? undefined),
    refetchInterval: 10_000,
    enabled: true,
  })

  const filtered =
    statusFilter === 'all'
      ? (commands ?? [])
      : (commands ?? []).filter((c) => c.status === statusFilter)

  const selectedSat = satList.find((s) => s.id === selectedSatelliteId)

  return (
    <div className="flex h-full">
      {/* Left: satellite selector */}
      <div className="flex w-60 flex-shrink-0 flex-col border-r border-space-border bg-space-panel">
        <div className="border-b border-space-border p-3">
          <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
            Satellites
          </h2>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          <button
            onClick={() => setSelectedSatelliteId(null)}
            className={clsx(
              'mb-2 w-full rounded px-3 py-2 text-left font-mono text-sm transition-colors',
              selectedSatelliteId === null
                ? 'bg-space-accent/20 text-space-accent'
                : 'text-gray-400 hover:text-white',
            )}
          >
            All Satellites
          </button>

          {loadingSats && (
            <p className="font-mono text-xs text-gray-500">Loading…</p>
          )}

          {satList.map((sat) => (
            <button
              key={sat.id}
              onClick={() => setSelectedSatelliteId(sat.id)}
              className={clsx(
                'mb-1 flex w-full items-center justify-between rounded px-3 py-2 text-left transition-colors',
                selectedSatelliteId === sat.id
                  ? 'bg-space-accent/20 text-space-accent'
                  : 'text-gray-400 hover:text-white',
              )}
            >
              <span className="font-mono text-sm truncate">{sat.name}</span>
              <ModeBadge mode={sat.mode} size="sm" />
            </button>
          ))}
        </div>
      </div>

      {/* Right: command queue */}
      <div className="flex flex-1 flex-col">
        {/* Toolbar */}
        <div className="flex items-center justify-between border-b border-space-border bg-space-panel px-6 py-3">
          <div className="flex items-center gap-3">
            <h2 className="font-mono text-sm font-semibold text-white">
              {selectedSat ? `Commands — ${selectedSat.name}` : 'All Commands'}
            </h2>
            {commands && (
              <span className="font-mono text-xs text-gray-500">
                {filtered.length} / {commands.length}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* Status filter */}
            <div className="flex items-center gap-2">
              <label className="font-mono text-xs text-gray-500">Status:</label>
              <select
                value={statusFilter}
                onChange={(e) =>
                  setStatusFilter(e.target.value as Command['status'] | 'all')
                }
                className="rounded border border-space-border bg-space-dark px-2 py-1 font-mono text-xs text-white outline-none"
              >
                <option value="all">All</option>
                {STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            {selectedSatelliteId && (
              <button
                onClick={() => setShowModal(true)}
                className="rounded bg-space-accent px-3 py-1.5 font-mono text-xs font-semibold text-white hover:bg-blue-500"
              >
                + Send Command
              </button>
            )}
          </div>
        </div>

        {/* Command list */}
        <div className="flex-1 overflow-y-auto p-6">
          {loadingCmds && (
            <p className="font-mono text-sm text-gray-500">Loading commands…</p>
          )}
          {isError && (
            <p className="font-mono text-sm text-red-400">Failed to load commands.</p>
          )}

          {filtered.length === 0 && !loadingCmds && (
            <div className="flex h-40 items-center justify-center">
              <p className="font-mono text-sm text-gray-500">
                {statusFilter !== 'all'
                  ? `No commands with status "${statusFilter}".`
                  : 'No commands found. Select a satellite and send one!'}
              </p>
            </div>
          )}

          <div className="space-y-2">
            {filtered.map((cmd) => (
              <CommandRow key={cmd.id} command={cmd} />
            ))}
          </div>
        </div>
      </div>

      {showModal && selectedSatelliteId && selectedSat && (
        <CommandModal
          satelliteId={selectedSatelliteId}
          satelliteMode={selectedSat.mode}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  )
}
