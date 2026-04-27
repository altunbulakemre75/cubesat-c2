/**
 * Formatting helpers — keeps display logic out of components so they can
 * be unit-tested in isolation.
 */

/**
 * "3s ago", "12m ago", "5h ago", "2d ago", or "No telemetry" when the input
 * is null/undefined. Future timestamps clamp to "just now".
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'No telemetry'
  const date = new Date(iso)
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000)
  if (diffSec < 0) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 48) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

/** Wall-clock HH:MM:SS in user's local timezone (always 24h). */
export function formatClockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
