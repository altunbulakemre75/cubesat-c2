/**
 * satellite.js wrappers — single place for SGP4 propagation.
 * Components should NOT import satellite.js directly; they call these
 * helpers so we can swap implementations or add caching later.
 */

import { twoline2satrec, propagate, gstime, eciToGeodetic, degreesLat, degreesLong } from 'satellite.js'

export interface GeodeticPosition {
  /** Latitude in degrees, -90 to +90 */
  lat: number
  /** Longitude in degrees, -180 to +180 */
  lon: number
  /** Altitude above the WGS-84 ellipsoid, in metres */
  altMeters: number
}

/**
 * Propagate a TLE to the given UTC time and return the subsatellite point
 * in geodetic coordinates. Returns null if SGP4 fails (decayed satellite,
 * malformed TLE, etc.) so callers can keep their last-known position.
 */
export function propagateToGeodetic(
  tleLine1: string,
  tleLine2: string,
  whenUtc: Date,
): GeodeticPosition | null {
  const satrec = twoline2satrec(tleLine1, tleLine2)
  const pv = propagate(satrec, whenUtc)
  if (!pv.position || typeof pv.position === 'boolean') return null

  const gst = gstime(whenUtc)
  const geo = eciToGeodetic(
    pv.position as { x: number; y: number; z: number },
    gst,
  )
  return {
    lat: degreesLat(geo.latitude),
    lon: degreesLong(geo.longitude),
    altMeters: geo.height * 1000,
  }
}

/**
 * Sample N positions along the next `lookaheadMinutes` of the orbit.
 * Useful for drawing the upcoming-orbit polyline.
 */
export function sampleOrbitTrail(
  tleLine1: string,
  tleLine2: string,
  startUtc: Date,
  lookaheadMinutes = 90,
  stepMinutes = 1,
): GeodeticPosition[] {
  const out: GeodeticPosition[] = []
  for (let i = 0; i <= lookaheadMinutes; i += stepMinutes) {
    const t = new Date(startUtc.getTime() + i * 60_000)
    const pos = propagateToGeodetic(tleLine1, tleLine2, t)
    if (pos) out.push(pos)
  }
  return out
}
