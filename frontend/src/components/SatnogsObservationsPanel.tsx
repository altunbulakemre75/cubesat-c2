import { useQuery } from '@tanstack/react-query'
import { fetchSatnogsObservations } from '../api/satnogs'

function formatRelative(iso: string): string {
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 48) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

function shortHex(hex: string | null, n = 24): string {
  if (!hex) return ''
  return hex.length > n ? `${hex.slice(0, n)}…` : hex
}

interface Props {
  satelliteId?: string
  noradId?: number
  limit?: number
}

export function SatnogsObservationsPanel({ satelliteId, noradId, limit = 10 }: Props) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['satnogs', 'observations', satelliteId ?? '*', noradId ?? '*'],
    queryFn: () => fetchSatnogsObservations({ satellite_id: satelliteId, norad_id: noradId, limit }),
    refetchInterval: 60_000,
  })

  if (isLoading) {
    return <p className="font-mono text-xs text-gray-500">Loading SatNOGS frames…</p>
  }
  if (isError) {
    return (
      <p className="font-mono text-xs text-red-400">
        SatNOGS load failed: {(error as Error).message}
      </p>
    )
  }
  if (!data || data.length === 0) {
    return (
      <div className="space-y-1 font-mono text-xs text-gray-500">
        <p>No SatNOGS frames yet.</p>
        <p className="text-gray-600">
          Background fetcher polls every 15 min. Real frames appear once amateurs
          worldwide upload them.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <p className="font-mono text-xs text-gray-500">
        Latest {data.length} demodulated frames from SatNOGS DB
      </p>
      <div className="flex flex-col gap-1">
        {data.map((obs) => (
          <div
            key={obs.id}
            className="rounded border border-space-border bg-space-panel/40 p-2 font-mono text-xs"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-semibold text-gray-200">
                {obs.satellite_id ?? `NORAD ${obs.norad_cat_id}`}
                <span className="ml-2 text-gray-500">
                  via {obs.observer ?? 'unknown'}
                </span>
              </span>
              <span className="text-gray-500">{formatRelative(obs.timestamp_utc)}</span>
            </div>
            {obs.decoded_json && Object.keys(obs.decoded_json).length > 0 ? (
              <pre className="mt-1 overflow-hidden text-emerald-300">
                {JSON.stringify(obs.decoded_json, null, 0).slice(0, 240)}
              </pre>
            ) : obs.frame_hex ? (
              <p className="mt-1 break-all text-gray-400">frame: {shortHex(obs.frame_hex, 64)}</p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}
