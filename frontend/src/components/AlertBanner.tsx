import { clsx } from 'clsx'
import { useAppStore } from '../store'
import type { AppEvent } from '../types'

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

interface AlertRowProps {
  alert: AppEvent
  onDismiss: (id: string) => void
}

function AlertRow({ alert, onDismiss }: AlertRowProps) {
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
          {alert.type.replace('_', ' ')}
          {alert.satellite_id && ` · ${alert.satellite_id}`}
        </p>
        <p className="mt-0.5 break-words">{alert.message}</p>
        <p className="mt-0.5 text-gray-500">{formatTime(alert.timestamp)}</p>
      </div>
      <button
        onClick={() => onDismiss(alert.id)}
        className="flex-shrink-0 text-gray-500 hover:text-white"
        aria-label="Dismiss alert"
      >
        ✕
      </button>
    </div>
  )
}

export function AlertBanner() {
  const activeAlerts = useAppStore((s) => s.activeAlerts)
  const dismissAlert = useAppStore((s) => s.dismissAlert)

  if (activeAlerts.length === 0) {
    return (
      <p className="font-mono text-xs text-gray-600">No active alerts</p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {activeAlerts.map((alert) => (
        <AlertRow key={alert.id} alert={alert} onDismiss={dismissAlert} />
      ))}
    </div>
  )
}
