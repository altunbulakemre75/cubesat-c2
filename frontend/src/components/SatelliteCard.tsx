import { clsx } from 'clsx'
import { ModeBadge } from './ModeBadge'
import { BatteryBar } from './BatteryBar'
import type { SatelliteListItem } from '../types'

interface SatelliteCardProps {
  satellite: SatelliteListItem
  isActive: boolean
  onClick: () => void
}

function formatLastSeen(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  return `${diffHr}h ago`
}

export function SatelliteCard({ satellite, isActive, onClick }: SatelliteCardProps) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full rounded-lg border p-3 text-left transition-all duration-150',
        'hover:border-space-accent hover:bg-space-accent/5',
        isActive
          ? 'border-space-accent bg-space-accent/10'
          : 'border-space-border bg-space-panel',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm font-semibold text-white">
            {satellite.name}
          </p>
          <p className="font-mono text-xs text-gray-500">
            ID: {satellite.id}
            {satellite.norad_id !== undefined && ` · NORAD ${satellite.norad_id}`}
          </p>
        </div>
        <ModeBadge mode={satellite.mode} size="sm" />
      </div>

      <div className="mt-2">
        <BatteryBar voltage={satellite.battery_voltage_v} />
      </div>

      <p className="mt-1 font-mono text-xs text-gray-500">
        Last seen: {formatLastSeen(satellite.last_seen)}
      </p>
    </button>
  )
}
