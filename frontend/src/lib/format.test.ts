import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { formatRelativeTime, formatClockTime } from './format'

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-27T12:00:00Z'))
  })
  afterEach(() => { vi.useRealTimers() })

  it('returns "No telemetry" for null', () => {
    expect(formatRelativeTime(null)).toBe('No telemetry')
  })
  it('returns "No telemetry" for undefined', () => {
    expect(formatRelativeTime(undefined)).toBe('No telemetry')
  })
  it('formats seconds', () => {
    expect(formatRelativeTime('2026-04-27T11:59:55Z')).toBe('5s ago')
  })
  it('formats minutes', () => {
    expect(formatRelativeTime('2026-04-27T11:55:00Z')).toBe('5m ago')
  })
  it('formats hours up to 48', () => {
    expect(formatRelativeTime('2026-04-27T05:00:00Z')).toBe('7h ago')
  })
  it('formats days past 48 hours', () => {
    expect(formatRelativeTime('2026-04-24T12:00:00Z')).toBe('3d ago')
  })
  it('clamps future timestamps to "just now"', () => {
    expect(formatRelativeTime('2026-04-27T12:00:30Z')).toBe('just now')
  })
})

describe('formatClockTime', () => {
  it('returns HH:MM:SS shape', () => {
    const out = formatClockTime('2026-04-27T12:34:56Z')
    expect(out).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })
})
