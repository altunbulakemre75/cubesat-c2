import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { TelemetryPoint } from '../types'

interface TelemetryChartProps {
  points: TelemetryPoint[]
}

interface ChartDatum {
  time: string
  battery_v: number
  temp_obcs: number
  solar_w: number
}

function toChartData(points: TelemetryPoint[]): ChartDatum[] {
  return points.map((p) => ({
    time: new Date(p.timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }),
    battery_v: p.params.battery_voltage_v,
    temp_obcs: p.params.temperature_obcs_c,
    solar_w: p.params.solar_power_w,
  }))
}

const TOOLTIP_STYLE = {
  backgroundColor: '#111827',
  border: '1px solid #1f2937',
  borderRadius: 6,
  color: '#f9fafb',
  fontSize: 11,
  fontFamily: 'JetBrains Mono, monospace',
}

export function TelemetryChart({ points }: TelemetryChartProps) {
  const data = toChartData(points)

  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded border border-space-border bg-space-panel">
        <p className="font-mono text-sm text-gray-500">Waiting for telemetry data…</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Battery Voltage */}
      <div>
        <h4 className="mb-1 font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
          Battery Voltage (V)
        </h4>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="time"
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              interval="preserveStartEnd"
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Line
              type="monotone"
              dataKey="battery_v"
              name="Battery (V)"
              stroke="#34d399"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* OBC Temperature */}
      <div>
        <h4 className="mb-1 font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
          OBC Temperature (°C)
        </h4>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="time"
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              interval="preserveStartEnd"
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Line
              type="monotone"
              dataKey="temp_obcs"
              name="OBC Temp (°C)"
              stroke="#f59e0b"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Solar Power */}
      <div>
        <h4 className="mb-1 font-mono text-xs font-semibold uppercase tracking-wider text-gray-400">
          Solar Power (W)
        </h4>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="time"
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              interval="preserveStartEnd"
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              tickLine={false}
              axisLine={false}
              domain={['auto', 'auto']}
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend
              wrapperStyle={{ fontSize: 10, fontFamily: 'monospace', color: '#9ca3af' }}
            />
            <Line
              type="monotone"
              dataKey="solar_w"
              name="Solar (W)"
              stroke="#818cf8"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
