import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from './index'

describe('useAppStore — auth slice', () => {
  beforeEach(() => {
    useAppStore.getState().clearAuth()
  })

  it('starts with no auth', () => {
    const s = useAppStore.getState()
    expect(s.token).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.username).toBeNull()
  })

  it('setAuth records token and username', () => {
    useAppStore.getState().setAuth('access-xyz', 'alice')
    const s = useAppStore.getState()
    expect(s.token).toBe('access-xyz')
    expect(s.username).toBe('alice')
  })

  it('setRefreshToken stores refresh independently of access', () => {
    useAppStore.getState().setAuth('access-xyz', 'alice')
    useAppStore.getState().setRefreshToken('refresh-abc')
    const s = useAppStore.getState()
    expect(s.refreshToken).toBe('refresh-abc')
    expect(s.token).toBe('access-xyz')   // still there
  })

  it('clearAuth wipes all three fields', () => {
    useAppStore.getState().setAuth('a', 'b')
    useAppStore.getState().setRefreshToken('c')
    useAppStore.getState().clearAuth()
    const s = useAppStore.getState()
    expect(s.token).toBeNull()
    expect(s.refreshToken).toBeNull()
    expect(s.username).toBeNull()
  })
})
