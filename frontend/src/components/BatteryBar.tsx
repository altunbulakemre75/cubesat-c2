import { clsx } from 'clsx'

interface BatteryBarProps {
  voltage: number | null
  /** Nominal full voltage. Default 4.2 V (single-cell Li-ion); pass
   *  8.4 for 2S LiPo or 12.6 for 3S as needed per satellite. */
  maxVoltage?: number
  /** Cutoff voltage below which we consider empty. Default 3.0 V (1S). */
  minVoltage?: number
}

export function BatteryBar({
  voltage,
  maxVoltage = 4.2,
  minVoltage = 3.0,
}: BatteryBarProps) {
  if (voltage === null || voltage === undefined) {
    return (
      <div className="flex items-center gap-2">
        <div className="h-4 w-24 rounded border border-gray-800 bg-gray-900" />
        <span className="font-mono text-xs text-gray-600">— V</span>
      </div>
    )
  }

  const pct = Math.max(
    0,
    Math.min(100, ((voltage - minVoltage) / (maxVoltage - minVoltage)) * 100),
  )

  const barColor =
    pct > 60 ? 'bg-green-500' : pct > 30 ? 'bg-yellow-400' : 'bg-red-500'

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex h-4 w-24 items-center rounded border border-space-border bg-space-dark">
        <div
          className={clsx('h-full rounded-sm transition-all duration-500', barColor)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-xs text-gray-400">{voltage.toFixed(2)} V</span>
    </div>
  )
}
