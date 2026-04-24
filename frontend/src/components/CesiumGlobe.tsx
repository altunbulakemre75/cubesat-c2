import { useEffect, useRef } from 'react'
import type { TLEData } from '../api/satellites'
import type { SatelliteListItem } from '../types'

let CesiumModule: typeof import('cesium') | null = null
async function loadCesium() {
  if (CesiumModule) return CesiumModule
  try { CesiumModule = await import('cesium'); return CesiumModule }
  catch { return null }
}

const CESIUM_TOKEN = import.meta.env.VITE_CESIUM_TOKEN as string | undefined

// Orbit trail: compute positions every 60s for the next 90 minutes (one LEO orbit)
const TRAIL_STEP_S = 60
const TRAIL_MINUTES = 90

interface Props {
  satellites: SatelliteListItem[]
  tles: TLEData[]
}

export function CesiumGlobe({ satellites, tles }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<import('cesium').Viewer | null>(null)
  const entitiesRef = useRef<Map<string, import('cesium').Entity>>(new Map())
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

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
      if (intervalRef.current) clearInterval(intervalRef.current)
      viewerRef.current?.destroy()
      viewerRef.current = null
    }
  }, [])

  // ── satellite entities + live update ─────────────────────────────────────
  useEffect(() => {
    if (!CESIUM_TOKEN || tles.length === 0) return
    if (intervalRef.current) clearInterval(intervalRef.current)

    const updatePositions = async () => {
      const viewer = viewerRef.current
      if (!viewer || viewer.isDestroyed()) return
      const Cesium = await loadCesium()
      if (!Cesium) return

      const { twoline2satrec, propagate, gstime, eciToGeodetic, degreesLat, degreesLong } =
        await import('satellite.js')

      const eciToCartesian = (
        pos: { x: number; y: number; z: number },
        gst: number,
        C: typeof Cesium,
      ): import('cesium').Cartesian3 | null => {
        const geo = eciToGeodetic(pos, gst)
        const lat = degreesLat(geo.latitude)
        const lon = degreesLong(geo.longitude)
        const altM = geo.height * 1000  // km → m
        return C.Cartesian3.fromDegrees(lon, lat, altM)
      }

      for (const tle of tles) {
        const satName = satellites.find(s => s.id === tle.satellite_id)?.name ?? tle.satellite_id
        const satrec = twoline2satrec(tle.tle_line1, tle.tle_line2)

        const now = new Date()
        const posVel = propagate(satrec, now)
        if (!posVel.position || typeof posVel.position === 'boolean') continue

        const gst = gstime(now)
        const cartesian = eciToCartesian(posVel.position as { x: number; y: number; z: number }, gst, Cesium)
        if (!cartesian) continue

        if (entitiesRef.current.has(tle.satellite_id)) {
          const entity = entitiesRef.current.get(tle.satellite_id)!
          ;(entity.position as unknown as { setValue: (v: typeof cartesian) => void })
            .setValue(cartesian)
        } else {
          // Compute orbit trail (next 90 min)
          const trailPositions: import('cesium').Cartesian3[] = []
          for (let i = 0; i <= TRAIL_MINUTES; i += TRAIL_STEP_S / 60) {
            const t = new Date(now.getTime() + i * 60 * 1000)
            const pv = propagate(satrec, t)
            if (!pv.position || typeof pv.position === 'boolean') continue
            const c = eciToCartesian(
              pv.position as { x: number; y: number; z: number },
              gstime(t),
              Cesium
            )
            if (c) trailPositions.push(c)
          }

          const entity = viewer.entities.add({
            name: satName,
            position: new Cesium.ConstantPositionProperty(cartesian),
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
              material: new Cesium.ColorMaterialProperty(
                Cesium.Color.CYAN.withAlpha(0.3)
              ),
            } : undefined,
          })
          entitiesRef.current.set(tle.satellite_id, entity)
        }
      }
    }

    void updatePositions()
    intervalRef.current = setInterval(() => { void updatePositions() }, 2000)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
      entitiesRef.current.forEach((_, id) => {
        viewerRef.current?.entities.removeById(id)
      })
      entitiesRef.current.clear()
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
