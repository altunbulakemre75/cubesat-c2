import { clsx } from 'clsx'

interface BatteryBarProps {
  voltage: number
  /** Nominal full voltage (default 8.4 V for 2S LiPo) */
  maxVoltage?: number
  /** Cutoff voltage below which we consider empty (default 6.0 V) */
  minVoltage?: number
}

export function BatteryBar({
  voltage,
  maxVoltage = 8.4,
  minVoltage = 6.0,
}: BatteryBarProps) {
  const pct = Math.max(
    0,
    Math.min(100, ((voltage - minVoltage) / (maxVoltage - minVoltage)) * 100),
  )

  const barColor =
    pct > 60
      ? 'bg-green-500'
      : pct > 30
        ? 'bg-yellow-400'
        : 'bg-red-500'

  return (
    <div className="flex items-center gap-2">
      {/* Battery icon shell */}
      <div className="relative flex h-4 w-24 items-center rounded border border-space-border bg-space-dark">
        <div
          className={clsx('h-full rounded-sm transition-all duration-500', barColor)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-xs text-gray-400">
        {voltage.toFixed(2)} V
      </span>
    </div>
  )
}
