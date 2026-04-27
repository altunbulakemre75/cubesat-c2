import { clsx } from 'clsx'
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '../store'
import { ackFDIRAlert, fetchFDIRAlerts } from '../api/fdir'
import type { AppEvent } from '../types'

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

interface AlertRowProps {
  alert: AppEvent
  onDismiss: (alert: AppEvent) => void
  acking: boolean
}

function AlertRow({ alert, onDismiss, acking }: AlertRowProps) {
  return (
    <div
      className={clsx(
        'flex items-start gap-2 rounded border p-2 text-xs font-mono',
        alert.severity === 'critical'
          ? 'border-red-700 bg-red-900/30 text-red-200'
          : 'border-yellow-700 bg-yellow-900/20 text-yellow-200',
      )}
    >
      <span
        className={clsx(
          'mt-0.5 h-2 w-2 flex-shrink-0 rounded-full',
          alert.severity === 'critical' ? 'bg-red-500' : 'bg-yellow-500',
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="font-semibold uppercase tracking-wider text-gray-400">
          {(alert.type ?? 'info').replace('_', ' ')}
          {alert.satellite_id && ` · ${alert.satellite_id}`}
        </p>
        <p className="mt-0.5 break-words">{alert.message}</p>
        <p className="mt-0.5 text-gray-500">
          {alert.timestamp ? formatTime(alert.timestamp) : ''}
        </p>
      </div>
      <button
        onClick={() => onDismiss(alert)}
        disabled={acking}
        className="flex-shrink-0 text-gray-500 hover:text-white disabled:opacity-40"
        aria-label={alert.type === 'fdir_alert' ? 'Acknowledge alert' : 'Dismiss alert'}
        title={alert.type === 'fdir_alert' ? 'Acknowledge (server-side)' : 'Dismiss'}
      >
        {acking ? '…' : '✕'}
      </button>
    </div>
  )
}

export function AlertBanner() {
  const activeAlerts = useAppStore((s) => s.activeAlerts)
  const dismissAlert = useAppStore((s) => s.dismissAlert)
  const pushEvent = useAppStore((s) => s.pushEvent)
  const qc = useQueryClient()

  // On mount, hydrate from the server so a freshly-loaded page shows
  // alerts that pre-date the websocket connection.
  const { data: serverAlerts } = useQuery({
    queryKey: ['fdir', 'alerts', 'unack'],
    queryFn: () => fetchFDIRAlerts({ unacknowledged_only: true, limit: 20 }),
    refetchInterval: 60_000,
  })

  useEffect(() => {
    if (!serverAlerts) return
    for (const a of serverAlerts) {
      pushEvent({
        id: a.id,
        type: 'fdir_alert',
        satellite_id: a.satellite_id,
        message: `FDIR alert: ${a.reason}`,
        timestamp: a.triggered_at,
        severity: a.severity,
      })
    }
  }, [serverAlerts, pushEvent])

  const ackMutation = useMutation({
    mutationFn: (id: string) => ackFDIRAlert(id),
    onSuccess: (_data, id) => {
      dismissAlert(id)
      qc.invalidateQueries({ queryKey: ['fdir', 'alerts'] })
    },
    onError: (err, id) => {
      // 409 (already acknowledged) is benign — drop the toast locally too.
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 404 || status === 409) {
        dismissAlert(id)
        return
      }
      console.warn('FDIR ack failed', err)
    },
  })

  const handleDismiss = (alert: AppEvent) => {
    if (alert.type === 'fdir_alert' && alert.id) {
      ackMutation.mutate(alert.id)
    } else {
      dismissAlert(alert.id)
    }
  }

  if (activeAlerts.length === 0) {
    return <p className="font-mono text-xs text-gray-600">No active alerts</p>
  }

  return (
    <div className="flex flex-col gap-2">
      {activeAlerts.map((alert) => (
        <AlertRow
          key={alert.id}
          alert={alert}
          onDismiss={handleDismiss}
          acking={
            ackMutation.isPending && ackMutation.variables === alert.id
          }
        />
      ))}
    </div>
  )
}
