import { describe, it, expect } from 'vitest'
import { sameOriginWsBase } from './urls'

describe('sameOriginWsBase', () => {
  // The UI talks to the backend through the serving origin (nginx proxies
  // /api and /ws), so the backend port never has to be exposed.
  it('uses ws:// on plain http', () => {
    expect(sameOriginWsBase({ protocol: 'http:', host: 'c2.local:3000' })).toBe('ws://c2.local:3000')
  })

  it('uses wss:// on https so browsers do not block mixed content', () => {
    expect(sameOriginWsBase({ protocol: 'https:', host: 'c2.example.org' })).toBe('wss://c2.example.org')
  })
})
