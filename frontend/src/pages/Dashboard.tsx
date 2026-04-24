import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { deleteSatellite, fetchAllTLEs, fetchSatellites } from '../api/satellites'
import { SatelliteCard } from '../components/SatelliteCard'
import { AlertBanner } from '../components/AlertBanner'
import { CesiumGlobe } from '../components/CesiumGlobe'
import { SatelliteAddModal } from '../components/SatelliteAddModal'
import { useAppStore } from '../store'
import type { AppEvent } from '../types'

function formatEventTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function EventFeedItem({ event }: { event: AppEvent }) {
  const colorMap: Record<string, string> = {
    fdir_alert: 'text-red-400',
    mode_change: 'text-blue-400',
    anomaly: 'text-yellow-400',
    command_ack: 'text-green-400',
    info: 'text-gray-400',
  }

  return (
    <div className="border-b border-space-border py-2 last:border-0">
      <div className="flex items-start gap-2">
        <span className={`font-mono text-xs font-semibold ${colorMap[event.type] ?? 'text-gray-400'}`}>
          [{event.type.toUpperCase().replace('_', ' ')}]
        </span>
      </div>
      <p className="font-mono text-xs text-gray-300">{event.message}</p>
      <p className="font-mono text-xs text-gray-600">{formatEventTime(event.timestamp)}</p>
    </div>
  )
}

export function Dashboard() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const activeSatelliteId = useAppStore((s) => s.activeSatelliteId)
  const setActiveSatelliteId = useAppStore((s) => s.setActiveSatelliteId)
  const events = useAppStore((s) => s.events)
  const [addOpen, setAddOpen] = useState(false)

  const {
    data: satellites,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ['satellites'],
    queryFn: fetchSatellites,
    refetchInterval: 30_000,
  })

  const { data: tles = [] } = useQuery({
    queryKey: ['tles', satellites?.map(s => s.id)],
    queryFn: () => fetchAllTLEs((satellites ?? []).map(s => s.id)),
    enabled: (satellites?.length ?? 0) > 0,
    refetchInterval: 300_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSatellite(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['satellites'] })
      qc.invalidateQueries({ queryKey: ['tles'] })
    },
  })

  return (
    <div className="flex h-full">
      {/* Left panel: satellite list */}
      <div className="flex w-60 flex-shrink-0 flex-col border-r border-space-border bg-space-panel">
        <div className="flex items-center justify-between border-b border-space-border p-3">
          <div>
            <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
              Satellites
            </h2>
            {satellites && (
              <p className="font-mono text-xs text-gray-600">
                {satellites.length} tracked
              </p>
            )}
          </div>
          <button
            onClick={() => setAddOpen(true)}
            className="rounded border border-space-accent bg-space-accent/10 px-2 py-1 font-mono text-xs text-space-accent hover:bg-space-accent hover:text-white"
            title="Add satellite"
          >
            + Add
          </button>
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto p-3">
          {isLoading && (
            <div className="space-y-2">
              {[1, 2, 3].map((n) => (
                <div
                  key={n}
                  className="h-20 animate-pulse rounded-lg bg-space-border"
                />
              ))}
            </div>
          )}

          {isError && (
            <p className="font-mono text-xs text-red-400">
              Failed to load satellites. Is the backend running?
            </p>
          )}

          {satellites?.map((sat) => (
            <SatelliteCard
              key={sat.id}
              satellite={sat}
              isActive={activeSatelliteId === sat.id}
              onClick={() => {
                setActiveSatelliteId(sat.id)
                navigate(`/satellites/${sat.id}`)
              }}
              onDelete={() => deleteMutation.mutate(sat.id)}
            />
          ))}

          {satellites?.length === 0 && (
            <p className="font-mono text-xs text-gray-500">No satellites found.</p>
          )}
        </div>
      </div>

      {/* Center: 3D Globe */}
      <div className="flex flex-1 flex-col">
        <div className="border-b border-space-border p-3">
          <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
            3D Orbit Visualization
          </h2>
        </div>
        <div className="flex-1 p-3">
          <CesiumGlobe satellites={satellites ?? []} tles={tles} />
        </div>
      </div>

      {/* Right panel: alerts + events */}
      <div className="flex w-72 flex-shrink-0 flex-col border-l border-space-border bg-space-panel">
        {/* Active alerts */}
        <div className="border-b border-space-border p-3">
          <h2 className="mb-2 font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
            Active Alerts
          </h2>
          <AlertBanner />
        </div>

        {/* Events feed */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="border-b border-space-border p-3">
            <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
              Event Feed
            </h2>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            {events.length === 0 ? (
              <p className="font-mono text-xs text-gray-600">
                No events yet. Waiting for WebSocket…
              </p>
            ) : (
              events.slice(0, 50).map((evt) => (
                <EventFeedItem key={evt.id} event={evt} />
              ))
            )}
          </div>
        </div>
      </div>

      <SatelliteAddModal open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  )
}
