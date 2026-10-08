// Which base imagery the 3D globe uses.
//
// With a Cesium Ion token: Ion's high-resolution imagery. Without one: the
// low-resolution Natural Earth II texture that ships inside Cesium. That
// needs no account and no network, so a fresh install (or an offline
// ground station) still shows satellites and orbits.
export type GlobeImagery =
  | { kind: 'ion'; token: string }
  | { kind: 'offline'; path: string }

// Example-file placeholder that people copy without replacing.
const PLACEHOLDERS = new Set(['your-cesium-ion-token-here'])

export const OFFLINE_IMAGERY_PATH = 'Assets/Textures/NaturalEarthII'

export function globeImagery(token: string | undefined): GlobeImagery {
  const usable = token?.trim()
  return usable && !PLACEHOLDERS.has(usable)
    ? { kind: 'ion', token: usable }
    : { kind: 'offline', path: OFFLINE_IMAGERY_PATH }
}
