import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BatteryBar } from './BatteryBar'

describe('BatteryBar', () => {
  it('shows placeholder when voltage is null', () => {
    render(<BatteryBar voltage={null} />)
    expect(screen.getByText(/— V/)).toBeInTheDocument()
  })

  it('shows formatted voltage when present', () => {
    render(<BatteryBar voltage={3.97} />)
    expect(screen.getByText(/3\.97 V/)).toBeInTheDocument()
  })

  it('clamps voltage above max to 100% (no overflow)', () => {
    const { container } = render(<BatteryBar voltage={5.0} />)
    const bar = container.querySelector('div[style*="width"]') as HTMLElement
    expect(bar.style.width).toBe('100%')
  })

  it('clamps voltage below min to 0% (no negative width)', () => {
    const { container } = render(<BatteryBar voltage={1.0} />)
    const bar = container.querySelector('div[style*="width"]') as HTMLElement
    expect(bar.style.width).toBe('0%')
  })
})
