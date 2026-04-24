import { clsx } from 'clsx'
import { ModeBadge } from './ModeBadge'
import { BatteryBar } from './BatteryBar'
import type { SatelliteListItem } from '../types'

interface SatelliteCardProps {
  satellite: SatelliteListItem
  isActive: boolean
  onClick: () => void
  onDelete?: () => void
}

function formatLastSeen(iso: string | null): string {
  if (!iso) return 'No telemetry'
  const date = new Date(iso)
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 48) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

export function SatelliteCard({ satellite, isActive, onClick, onDelete }: SatelliteCardProps) {
  const noradLabel =
    satellite.norad_id != null ? ` · NORAD ${satellite.norad_id}` : ''

  return (
    <div
      className={clsx(
        'group relative w-full rounded-lg border p-3 text-left transition-all duration-150',
        'hover:border-space-accent hover:bg-space-accent/5 cursor-pointer',
        isActive
          ? 'border-space-accent bg-space-accent/10'
          : 'border-space-border bg-space-panel',
      )}
      onClick={onClick}
    >
      {onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            if (confirm(`Delete satellite "${satellite.name}"?\nThis removes the DB record and telemetry history.`)) {
              onDelete()
            }
          }}
          className="absolute right-2 top-2 rounded p-1 text-gray-600 opacity-0 hover:bg-red-900/40 hover:text-red-400 group-hover:opacity-100"
          title="Delete satellite"
          aria-label="Delete satellite"
        >
          ✕
        </button>
      )}

      <div className="flex items-start justify-between gap-2 pr-5">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm font-semibold text-white">
            {satellite.name}
          </p>
          <p className="font-mono text-xs text-gray-500">
            ID: {satellite.id}{noradLabel}
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
    </div>
  )
}
