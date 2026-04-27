import { useEffect, useRef } from 'react'
import type { TLEData } from '../api/satellites'
import type { SatelliteListItem } from '../types'

let CesiumModule: typeof import('cesium') | null = null
async function loadCesium() {
  if (CesiumModule) return CesiumModule
  try { CesiumModule = await import('cesium'); return CesiumModule }
  catch { return null }
}

let SatjsModule: typeof import('satellite.js') | null = null
async function loadSatjs() {
  if (SatjsModule) return SatjsModule
  try { SatjsModule = await import('satellite.js'); return SatjsModule }
  catch { return null }
}

const CESIUM_TOKEN = import.meta.env.VITE_CESIUM_TOKEN as string | undefined

// Orbit trail: 90-minute lookahead polyline (one LEO orbit)
const TRAIL_STEP_MIN = 1
const TRAIL_MINUTES = 90

interface Props {
  satellites: SatelliteListItem[]
  tles: TLEData[]
}

export function CesiumGlobe({ satellites, tles }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<import('cesium').Viewer | null>(null)
  const entityIdsRef = useRef<Set<string>>(new Set())

  // ── initial viewer setup ──────────────────────────────────────────────────
  useEffect(() => {
    if (!CESIUM_TOKEN || !containerRef.current) return
    let cancelled = false

    void (async () => {
      const Cesium = await loadCesium()
      if (!Cesium || cancelled || !containerRef.current) return

      Cesium.Ion.defaultAccessToken = CESIUM_TOKEN
      const viewer = new Cesium.Viewer(containerRef.current, {
        terrainProvider: undefined,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
      })
      viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#0a0e1a')
      viewer.scene.globe.enableLighting = true
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(35.0, 39.0, 18_000_000),
      })
      viewerRef.current = viewer
    })()

    return () => {
      cancelled = true
      viewerRef.current?.destroy()
      viewerRef.current = null
      entityIdsRef.current.clear()
    }
  }, [])

  // ── satellite entities using CallbackProperty (Cesium recomputes each frame) ─
  useEffect(() => {
    if (!CESIUM_TOKEN) return
    let cancelled = false

    void (async () => {
      const viewer = viewerRef.current
      if (!viewer || viewer.isDestroyed()) return
      const Cesium = await loadCesium()
      const satjs = await loadSatjs()
      if (cancelled || !Cesium || !satjs) return

      const { twoline2satrec, propagate, gstime, eciToGeodetic, degreesLat, degreesLong } = satjs

      // Remove stale entities (satellites no longer in the TLE list)
      const wantedIds = new Set(tles.map(t => t.satellite_id))
      for (const id of Array.from(entityIdsRef.current)) {
        if (!wantedIds.has(id)) {
          viewer.entities.removeById(id)
          entityIdsRef.current.delete(id)
        }
      }

      const eciToCartesian = (pos: { x: number; y: number; z: number }, gst: number) => {
        const geo = eciToGeodetic(pos, gst)
        return Cesium.Cartesian3.fromDegrees(
          degreesLong(geo.longitude),
          degreesLat(geo.latitude),
          geo.height * 1000,
        )
      }

      for (const tle of tles) {
        if (entityIdsRef.current.has(tle.satellite_id)) continue

        const satName = satellites.find(s => s.id === tle.satellite_id)?.name ?? tle.satellite_id
        const satrec = twoline2satrec(tle.tle_line1, tle.tle_line2)

        // Precompute orbit trail (static 90-min future polyline)
        const now = new Date()
        const trailPositions: import('cesium').Cartesian3[] = []
        for (let i = 0; i <= TRAIL_MINUTES; i += TRAIL_STEP_MIN) {
          const t = new Date(now.getTime() + i * 60 * 1000)
          const pv = propagate(satrec, t)
          if (!pv.position || typeof pv.position === 'boolean') continue
          trailPositions.push(
            eciToCartesian(pv.position as { x: number; y: number; z: number }, gstime(t)),
          )
        }

        // CallbackProperty for position — Cesium invokes this every render
        // frame (~30-60 fps). Without throttling, N satellites = N × 60
        // SGP4 propagations/sec on the main thread → page locks at 50+ sats.
        // We cache the last result for 1 s; a LEO sat moves ~7 km/sec which
        // is invisible at the dashboard's 18,000 km default zoom.
        let lastT = 0
        let lastPos: import('cesium').Cartesian3 | undefined
        const positionCb = new Cesium.CallbackProperty(() => {
          const now = Date.now()
          if (lastPos && now - lastT < 1000) return lastPos
          const t = new Date(now)
          const pv = propagate(satrec, t)
          if (!pv.position || typeof pv.position === 'boolean') return lastPos
          lastPos = eciToCartesian(
            pv.position as { x: number; y: number; z: number },
            gstime(t),
          )
          lastT = now
          return lastPos
        }, false) as unknown as import('cesium').PositionProperty

        viewer.entities.add({
          id: tle.satellite_id,
          name: satName,
          position: positionCb,
          point: {
            pixelSize: 9,
            color: Cesium.Color.CYAN,
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1,
          },
          label: {
            text: satName,
            font: '11px monospace',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cesium.Cartesian2(0, -18),
          },
          polyline: trailPositions.length > 1 ? {
            positions: trailPositions,
            width: 1,
            material: new Cesium.ColorMaterialProperty(Cesium.Color.CYAN.withAlpha(0.3)),
          } : undefined,
        })
        entityIdsRef.current.add(tle.satellite_id)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [tles, satellites])

  if (!CESIUM_TOKEN) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center rounded-lg border border-gray-800 bg-gray-900">
        <div className="text-center">
          <div className="mb-3 text-5xl">🛰️</div>
          <p className="text-sm font-semibold text-gray-300">3D Globe Unavailable</p>
          <p className="mt-1 max-w-xs text-xs text-gray-500">
            Set <code className="rounded bg-gray-800 px-1 text-yellow-400">VITE_CESIUM_TOKEN</code>{' '}
            in <code className="rounded bg-gray-800 px-1 text-yellow-400">.env</code>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="h-full w-full rounded-lg overflow-hidden" style={{ minHeight: 400 }} />
  )
}
