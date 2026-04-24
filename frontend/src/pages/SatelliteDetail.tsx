import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { fetchSatellite } from '../api/satellites'
import { fetchTelemetry } from '../api/telemetry'
import { fetchCommands } from '../api/commands'
import { fetchPasses, fetchAnomalies } from '../api/passes'
import { useAppStore } from '../store'
import { useTelemetryWS } from '../hooks/useTelemetryWS'
import { TelemetryChart } from '../components/TelemetryChart'
import { ModeBadge } from '../components/ModeBadge'
import { CommandModal } from '../components/CommandModal'
import type { Command, Anomaly, Pass } from '../types'

type TabKey = 'telemetry' | 'commands' | 'passes' | 'anomalies'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'telemetry', label: 'Telemetry' },
  { key: 'commands', label: 'Commands' },
  { key: 'passes', label: 'Passes' },
  { key: 'anomalies', label: 'Anomalies' },
]

const COMMAND_STATUS_STYLES: Record<Command['status'], string> = {
  pending: 'bg-gray-700 text-gray-300',
  scheduled: 'bg-blue-800 text-blue-200',
  transmitting: 'bg-yellow-800 text-yellow-200',
  sent: 'bg-cyan-800 text-cyan-200',
  acked: 'bg-green-800 text-green-200',
  timeout: 'bg-orange-800 text-orange-200',
  retry: 'bg-purple-800 text-purple-200',
  dead: 'bg-red-900 text-red-300',
}

function formatIso(iso: string): string {
  return new Date(iso).toLocaleString()
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  return `${m}m ${s}s`
}

// ----- Sub-tabs -----

function TelemetryTab({ satelliteId }: { satelliteId: string }) {
  useTelemetryWS(satelliteId)

  const telemetryWindow = useAppStore((s) => s.telemetryWindows[satelliteId] ?? [])

  const { data: initialPoints, isLoading } = useQuery({
    queryKey: ['telemetry', satelliteId],
    queryFn: () => fetchTelemetry(satelliteId, { limit: 100 }),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const displayPoints =
    telemetryWindow.length > 0 ? telemetryWindow : (initialPoints ?? [])

  if (isLoading && displayPoints.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center">
        <p className="font-mono text-sm text-gray-500">Loading telemetry…</p>
      </div>
    )
  }

  // Show latest params summary
  const latest = displayPoints[displayPoints.length - 1]

  return (
    <div className="space-y-6">
      {latest && (
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          {(
            [
              { label: 'Battery', value: `${latest.params.battery_voltage_v.toFixed(2)} V`, color: 'text-green-400' },
              { label: 'OBC Temp', value: `${latest.params.temperature_obcs_c.toFixed(1)} °C`, color: 'text-yellow-400' },
              { label: 'EPS Temp', value: `${latest.params.temperature_eps_c.toFixed(1)} °C`, color: 'text-orange-400' },
              { label: 'Solar', value: `${latest.params.solar_power_w.toFixed(2)} W`, color: 'text-purple-400' },
              { label: 'RSSI', value: `${latest.params.rssi_dbm.toFixed(0)} dBm`, color: 'text-blue-400' },
              { label: 'Seq', value: String(latest.sequence), color: 'text-gray-300' },
            ] as const
          ).map((stat) => (
            <div key={stat.label} className="rounded border border-space-border bg-space-dark p-2">
              <p className="font-mono text-xs text-gray-500">{stat.label}</p>
              <p className={clsx('font-mono text-sm font-semibold', stat.color)}>
                {stat.value}
              </p>
            </div>
          ))}
        </div>
      )}

      <TelemetryChart points={displayPoints} />
    </div>
  )
}

function CommandsTab({
  satelliteId,
}: {
  satelliteId: string
}) {
  const [showModal, setShowModal] = useState(false)
  const [statusFilter, setStatusFilter] = useState<Command['status'] | 'all'>('all')

  const { data: commands, isLoading, isError } = useQuery({
    queryKey: ['commands', satelliteId],
    queryFn: () => fetchCommands(satelliteId),
    refetchInterval: 10_000,
  })

  const filtered =
    statusFilter === 'all'
      ? (commands ?? [])
      : (commands ?? []).filter((c) => c.status === statusFilter)

  // Determine satellite mode for modal
  const satMode = useAppStore((s) =>
    s.satellites.find((sat) => sat.id === satelliteId)?.mode ?? 'nominal',
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <label className="font-mono text-xs text-gray-500">Filter:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as Command['status'] | 'all')}
            className="rounded border border-space-border bg-space-dark px-2 py-1 font-mono text-xs text-white outline-none"
          >
            <option value="all">All</option>
            {(['pending', 'scheduled', 'transmitting', 'sent', 'acked', 'timeout', 'retry', 'dead'] as const).map(
              (s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ),
            )}
          </select>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="rounded bg-space-accent px-3 py-1.5 font-mono text-xs font-semibold text-white hover:bg-blue-500"
        >
          + Send Command
        </button>
      </div>

      {isLoading && (
        <p className="font-mono text-sm text-gray-500">Loading commands…</p>
      )}
      {isError && (
        <p className="font-mono text-sm text-red-400">Failed to load commands.</p>
      )}

      {filtered.length === 0 && !isLoading && (
        <p className="font-mono text-sm text-gray-500">No commands match filter.</p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-xs">
          <thead>
            <tr className="border-b border-space-border text-left text-gray-500">
              <th className="pb-2 pr-4">Type</th>
              <th className="pb-2 pr-4">Status</th>
              <th className="pb-2 pr-4">Created</th>
              <th className="pb-2">ID</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((cmd) => (
              <tr
                key={cmd.id}
                className="border-b border-space-border/50 hover:bg-space-border/20"
              >
                <td className="py-2 pr-4 text-white">{cmd.command_type}</td>
                <td className="py-2 pr-4">
                  <span
                    className={clsx(
                      'rounded px-1.5 py-0.5 text-xs font-semibold uppercase',
                      COMMAND_STATUS_STYLES[cmd.status],
                    )}
                  >
                    {cmd.status}
                  </span>
                </td>
                <td className="py-2 pr-4 text-gray-400">{formatIso(cmd.created_at)}</td>
                <td className="py-2 font-mono text-gray-600 text-xs">
                  {cmd.id.slice(0, 8)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showModal && (
        <CommandModal
          satelliteId={satelliteId}
          satelliteMode={satMode}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  )
}

function PassesTab({ satelliteId }: { satelliteId: string }) {
  const { data: passes, isLoading, isError } = useQuery({
    queryKey: ['passes', satelliteId],
    queryFn: () => fetchPasses(satelliteId),
  })

  const upcoming = (passes ?? [])
    .filter((p) => new Date(p.aos) > new Date())
    .sort((a, b) => new Date(a.aos).getTime() - new Date(b.aos).getTime())
    .slice(0, 5)

  if (isLoading)
    return <p className="font-mono text-sm text-gray-500">Loading passes…</p>
  if (isError)
    return <p className="font-mono text-sm text-red-400">Failed to load passes.</p>

  if (upcoming.length === 0)
    return <p className="font-mono text-sm text-gray-500">No upcoming passes found.</p>

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {upcoming.map((pass: Pass, idx: number) => (
        <div
          key={`${pass.station_id}-${pass.aos}`}
          className="rounded-lg border border-space-border bg-space-dark p-3"
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-xs font-semibold text-space-accent">
              Pass #{idx + 1}
            </span>
            <span className="font-mono text-xs text-gray-400">{pass.station_name}</span>
          </div>
          <div className="mt-2 space-y-1 font-mono text-xs">
            <div className="flex justify-between">
              <span className="text-gray-500">AOS</span>
              <span className="text-white">{formatIso(pass.aos)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">LOS</span>
              <span className="text-white">{formatIso(pass.los)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Max El.</span>
              <span className="text-green-400">{pass.max_elevation_deg.toFixed(1)}°</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Az @ AOS</span>
              <span className="text-blue-400">{pass.azimuth_at_aos_deg.toFixed(1)}°</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function AnomaliesTab({ satelliteId }: { satelliteId: string }) {
  const { data: anomalies, isLoading, isError } = useQuery({
    queryKey: ['anomalies', satelliteId],
    queryFn: () => fetchAnomalies(satelliteId),
    refetchInterval: 30_000,
  })

  if (isLoading)
    return <p className="font-mono text-sm text-gray-500">Loading anomalies…</p>
  if (isError)
    return <p className="font-mono text-sm text-red-400">Failed to load anomalies.</p>
  if (!anomalies || anomalies.length === 0)
    return (
      <p className="font-mono text-sm text-gray-500">No anomalies detected. System nominal.</p>
    )

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-xs">
        <thead>
          <tr className="border-b border-space-border text-left text-gray-500">
            <th className="pb-2 pr-4">Severity</th>
            <th className="pb-2 pr-4">Parameter</th>
            <th className="pb-2 pr-4">Value</th>
            <th className="pb-2 pr-4">Z-Score</th>
            <th className="pb-2">Detected At</th>
          </tr>
        </thead>
        <tbody>
          {(anomalies as Anomaly[]).map((a) => (
            <tr
              key={a.id}
              className="border-b border-space-border/50 hover:bg-space-border/20"
            >
              <td className="py-2 pr-4">
                <span
                  className={clsx(
                    'rounded px-1.5 py-0.5 text-xs font-semibold uppercase',
                    a.severity === 'critical'
                      ? 'bg-red-900 text-red-300'
                      : 'bg-yellow-900 text-yellow-300',
                  )}
                >
                  {a.severity}
                </span>
              </td>
              <td className="py-2 pr-4 text-white">{a.parameter}</td>
              <td className="py-2 pr-4 text-gray-300">{a.value.toFixed(3)}</td>
              <td className="py-2 pr-4">
                <span
                  className={clsx(
                    a.z_score > 3 ? 'text-red-400' : 'text-yellow-400',
                  )}
                >
                  {a.z_score.toFixed(2)}σ
                </span>
              </td>
              <td className="py-2 text-gray-400">{formatIso(a.detected_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ----- Main page -----

export function SatelliteDetail() {
  const { id } = useParams<{ id: string }>()
  const [activeTab, setActiveTab] = useState<TabKey>('telemetry')

  const { data: satellite, isLoading, isError } = useQuery({
    queryKey: ['satellite', id],
    queryFn: () => fetchSatellite(id!),
    enabled: Boolean(id),
  })

  // Get latest uptime from telemetry store
  const latestTelemetry = useAppStore((s) =>
    id ? s.telemetryWindows[id]?.slice(-1)[0] : undefined,
  )

  if (!id) {
    return <p className="p-6 font-mono text-red-400">Invalid satellite ID.</p>
  }

  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <p className="font-mono text-sm text-gray-500">Loading satellite…</p>
      </div>
    )
  }

  if (isError || !satellite) {
    return (
      <div className="p-6">
        <p className="font-mono text-red-400">Failed to load satellite data.</p>
        <Link to="/" className="mt-2 block font-mono text-xs text-space-accent hover:underline">
          ← Back to Dashboard
        </Link>
      </div>
    )
  }

  const uptime = latestTelemetry?.params.uptime_s ?? null

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-space-border bg-space-panel px-6 py-4">
        <div className="flex items-center gap-2">
          <Link
            to="/"
            className="font-mono text-xs text-gray-500 hover:text-white"
          >
            ← Dashboard
          </Link>
          <span className="text-gray-700">/</span>
          <span className="font-mono text-xs text-gray-400">Satellites</span>
          <span className="text-gray-700">/</span>
          <span className="font-mono text-xs text-white">{satellite.name}</span>
        </div>

        <div className="mt-3 flex items-center gap-4">
          <h1 className="font-mono text-xl font-bold text-white">{satellite.name}</h1>
          <ModeBadge mode={satellite.mode} />
          {uptime !== null && (
            <span className="font-mono text-xs text-gray-500">
              Uptime: {formatUptime(uptime)}
            </span>
          )}
          {satellite.norad_id !== undefined && (
            <span className="font-mono text-xs text-gray-500">
              NORAD: {satellite.norad_id}
            </span>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-space-border bg-space-panel px-6">
        <div className="flex gap-0">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={clsx(
                'border-b-2 px-4 py-3 font-mono text-sm transition-colors',
                activeTab === tab.key
                  ? 'border-space-accent text-space-accent'
                  : 'border-transparent text-gray-500 hover:text-gray-300',
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'telemetry' && <TelemetryTab satelliteId={id} />}
        {activeTab === 'commands' && (
          <CommandsTab satelliteId={id} />
        )}
        {activeTab === 'passes' && <PassesTab satelliteId={id} />}
        {activeTab === 'anomalies' && <AnomaliesTab satelliteId={id} />}
      </div>
    </div>
  )
}
