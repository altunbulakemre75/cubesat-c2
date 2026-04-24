import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { fetchSatellites } from '../api/satellites'
import { fetchPasses } from '../api/passes'
import { useAppStore } from '../store'
import type { Pass, SatelliteListItem } from '../types'

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
  })
}

function passDurationMin(pass: Pass): number {
  return (new Date(pass.los).getTime() - new Date(pass.aos).getTime()) / 60_000
}

/** Returns left% and width% of the pass bar within a 24-hour window */
function passBarStyle(pass: Pass, windowStart: number): { left: string; width: string } {
  const aos = new Date(pass.aos).getTime()
  const los = new Date(pass.los).getTime()
  const left = Math.max(0, ((aos - windowStart) / DAY_MS) * 100)
  const width = Math.max(0.5, ((los - aos) / DAY_MS) * 100)
  return {
    left: `${left.toFixed(2)}%`,
    width: `${Math.min(width, 100 - left).toFixed(2)}%`,
  }
}

function HourTicks({ windowStart }: { windowStart: number }) {
  const hours = Array.from({ length: 25 }, (_, i) => i)

  return (
    <div className="relative h-6 border-b border-space-border">
      {hours.map((h) => {
        const pct = (h / 24) * 100
        const label = new Date(windowStart + h * HOUR_MS).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
        })
        return (
          <div
            key={h}
            className="absolute flex flex-col items-center"
            style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}
          >
            <div className="h-2 w-px bg-space-border" />
            {h % 3 === 0 && (
              <span className="font-mono text-xs text-gray-600">{label}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

interface SatPassRowProps {
  satellite: SatelliteListItem
  windowStart: number
}

function SatPassRow({ satellite, windowStart }: SatPassRowProps) {
  const windowEnd = windowStart + DAY_MS

  const { data: passes, isLoading } = useQuery({
    queryKey: ['passes', satellite.id],
    queryFn: () => fetchPasses(satellite.id),
    staleTime: 5 * 60_000,
  })

  const visiblePasses = (passes ?? []).filter(
    (p) =>
      new Date(p.los).getTime() > windowStart &&
      new Date(p.aos).getTime() < windowEnd,
  )

  return (
    <div className="flex items-stretch border-b border-space-border">
      {/* Satellite name column */}
      <div className="flex w-44 flex-shrink-0 items-center border-r border-space-border px-3 py-2">
        <div>
          <p className="font-mono text-xs font-semibold text-white truncate">
            {satellite.name}
          </p>
          <p className="font-mono text-xs text-gray-500">{satellite.id}</p>
        </div>
      </div>

      {/* Timeline row */}
      <div className="relative flex-1 py-2" style={{ minHeight: 40 }}>
        {isLoading && (
          <div className="absolute inset-0 flex items-center px-4">
            <div className="h-1 w-24 animate-pulse rounded bg-space-border" />
          </div>
        )}

        {visiblePasses.map((pass, idx) => {
          const { left, width } = passBarStyle(pass, windowStart)
          const duration = passDurationMin(pass)
          const isHigh = pass.max_elevation_deg > 30

          return (
            <div
              key={`${pass.station_id}-${pass.aos}-${idx}`}
              className="group absolute top-1/2 -translate-y-1/2"
              style={{ left, width }}
              title={`${pass.station_name}\nAOS: ${formatTime(pass.aos)}\nLOS: ${formatTime(pass.los)}\nMax El: ${pass.max_elevation_deg.toFixed(1)}°\nDuration: ${duration.toFixed(1)} min`}
            >
              <div
                className={clsx(
                  'h-5 rounded text-xs transition-opacity',
                  isHigh
                    ? 'bg-green-600 hover:bg-green-500'
                    : 'bg-blue-700 hover:bg-blue-600',
                )}
              />
              {/* Tooltip on hover */}
              <div className="pointer-events-none absolute bottom-7 left-0 z-10 hidden min-w-max rounded border border-space-border bg-space-panel px-2 py-1 font-mono text-xs text-white shadow-lg group-hover:block">
                <p className="font-semibold">{pass.station_name}</p>
                <p className="text-gray-400">
                  {formatTime(pass.aos)} — {formatTime(pass.los)} ({duration.toFixed(1)} min)
                </p>
                <p className="text-green-400">Max El: {pass.max_elevation_deg.toFixed(1)}°</p>
              </div>
            </div>
          )
        })}

        {!isLoading && visiblePasses.length === 0 && (
          <div className="absolute inset-0 flex items-center px-4">
            <span className="font-mono text-xs text-gray-700">No passes in window</span>
          </div>
        )}
      </div>
    </div>
  )
}

export function PassSchedule() {
  const storeSatellites = useAppStore((s) => s.satellites)

  const { data: fetchedSatellites, isLoading } = useQuery({
    queryKey: ['satellites'],
    queryFn: fetchSatellites,
    refetchInterval: 60_000,
  })

  const satellites = fetchedSatellites ?? storeSatellites

  // Window = start of today (local) for 24 hours
  const windowStart = (() => {
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    return d.getTime()
  })()

  const windowEnd = windowStart + DAY_MS

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-space-border bg-space-panel px-6 py-4">
        <h1 className="font-mono text-lg font-bold text-white">Pass Schedule</h1>
        <p className="font-mono text-xs text-gray-500">
          Gantt — {formatDate(new Date(windowStart).toISOString())} to{' '}
          {formatDate(new Date(windowEnd).toISOString())} (local time)
        </p>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 border-b border-space-border px-6 py-2">
        <div className="flex items-center gap-1.5">
          <div className="h-3 w-6 rounded bg-green-600" />
          <span className="font-mono text-xs text-gray-400">Max El &gt; 30°</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="h-3 w-6 rounded bg-blue-700" />
          <span className="font-mono text-xs text-gray-400">Max El ≤ 30°</span>
        </div>
        <span className="font-mono text-xs text-gray-600">Hover a bar for details</span>
      </div>

      {/* Timeline */}
      <div className="flex-1 overflow-auto">
        <div className="min-w-[900px]">
          {/* Header row */}
          <div className="flex border-b border-space-border">
            <div className="w-44 flex-shrink-0 border-r border-space-border px-3 py-2">
              <span className="font-mono text-xs font-semibold uppercase tracking-wider text-gray-500">
                Satellite
              </span>
            </div>
            <div className="flex-1">
              <HourTicks windowStart={windowStart} />
            </div>
          </div>

          {isLoading && satellites.length === 0 ? (
            <div className="flex h-40 items-center justify-center">
              <p className="font-mono text-sm text-gray-500">Loading satellites…</p>
            </div>
          ) : (
            satellites.map((sat) => (
              <SatPassRow key={sat.id} satellite={sat} windowStart={windowStart} />
            ))
          )}

          {satellites.length === 0 && !isLoading && (
            <div className="flex h-40 items-center justify-center">
              <p className="font-mono text-sm text-gray-500">
                No satellites tracked. Start the backend and refresh.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
