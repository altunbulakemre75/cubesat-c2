import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ModeBadge } from './ModeBadge'

describe('ModeBadge', () => {
  it('renders unknown badge when mode is null', () => {
    render(<ModeBadge mode={null} />)
    expect(screen.getByText(/unknown/i)).toBeInTheDocument()
  })

  it('renders the mode label uppercase', () => {
    render(<ModeBadge mode="nominal" />)
    expect(screen.getByText(/nominal/i)).toBeInTheDocument()
  })

  it('renders all valid modes without crashing', () => {
    for (const m of ['beacon', 'deployment', 'nominal', 'science', 'safe'] as const) {
      const { unmount } = render(<ModeBadge mode={m} />)
      expect(screen.getByText(new RegExp(m, 'i'))).toBeInTheDocument()
      unmount()
    }
  })
})
