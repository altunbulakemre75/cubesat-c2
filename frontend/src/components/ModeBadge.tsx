import { clsx } from 'clsx'
import type { SatelliteMode } from '../types'

interface ModeBadgeProps {
  mode: SatelliteMode
  size?: 'sm' | 'md'
}

const MODE_STYLES: Record<SatelliteMode, string> = {
  beacon: 'bg-gray-600 text-gray-200 border-gray-500',
  deployment: 'bg-blue-700 text-blue-100 border-blue-500',
  nominal: 'bg-green-700 text-green-100 border-green-500',
  science: 'bg-purple-700 text-purple-100 border-purple-500',
  safe: 'bg-red-700 text-red-100 border-red-500',
}

const MODE_DOT: Record<SatelliteMode, string> = {
  beacon: 'bg-gray-400',
  deployment: 'bg-blue-400',
  nominal: 'bg-green-400',
  science: 'bg-purple-400',
  safe: 'bg-red-400',
}

export function ModeBadge({ mode, size = 'md' }: ModeBadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded border font-mono font-semibold uppercase tracking-wider',
        MODE_STYLES[mode],
        size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-xs',
      )}
    >
      <span className={clsx('h-1.5 w-1.5 rounded-full', MODE_DOT[mode])} />
      {mode}
    </span>
  )
}
