import { describe, it, expect } from 'vitest'
import { globeImagery } from './globeImagery'

describe('globeImagery', () => {
  // A fresh install has no Cesium Ion account. The globe must still work
  // (satellites, orbits) with the imagery that ships inside Cesium.
  it('falls back to the bundled offline imagery without a token', () => {
    expect(globeImagery(undefined)).toEqual({
      kind: 'offline', path: 'Assets/Textures/NaturalEarthII',
    })
    expect(globeImagery('')).toEqual({
      kind: 'offline', path: 'Assets/Textures/NaturalEarthII',
    })
  })

  it('treats the .env.example placeholder as no token', () => {
    // Copying .env.example verbatim produced a 401 "Invalid access token"
    // and a black globe.
    expect(globeImagery('your-cesium-ion-token-here').kind).toBe('offline')
  })

  it('uses Cesium Ion imagery when a token is configured', () => {
    expect(globeImagery('abc.def')).toEqual({ kind: 'ion', token: 'abc.def' })
  })
})
