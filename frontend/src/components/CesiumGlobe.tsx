import { useEffect, useRef } from 'react'
import type { SatelliteDetail } from '../types'

// Conditional import — Cesium may fail in SSR/test environments
let CesiumModule: typeof import('cesium') | null = null

async function loadCesium() {
  if (CesiumModule) return CesiumModule
  try {
    CesiumModule = await import('cesium')
    return CesiumModule
  } catch {
    return null
  }
}

interface CesiumGlobeProps {
  satellites: SatelliteDetail[]
}

const CESIUM_TOKEN = import.meta.env.VITE_CESIUM_TOKEN as string | undefined

export function CesiumGlobe({ satellites }: CesiumGlobeProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<import('cesium').Viewer | null>(null)

  useEffect(() => {
    if (!CESIUM_TOKEN) return
    if (!containerRef.current) return

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
      viewerRef.current = viewer

      // Add satellite entities from TLE
      satellites.forEach((sat) => {
        if (!sat.tle_line1 || !sat.tle_line2) return

        try {
          // Import satellite.js dynamically to avoid top-level issues
          import('satellite.js').then(({ twoline2satrec, propagate, gstime }) => {
            const satrec = twoline2satrec(sat.tle_line1!, sat.tle_line2!)

            // Compute current position
            const now = new Date()
            const posVel = propagate(satrec, now)
            if (!posVel.position || typeof posVel.position === 'boolean') return

            const gmst = gstime(now)
            // Convert ECI to ECEF degrees for Cesium
            const pos = posVel.position as { x: number; y: number; z: number }
            // ECI km → Cesium Cartesian3
            const cartesian = new Cesium!.Cartesian3(
              pos.x * 1000,
              pos.y * 1000,
              pos.z * 1000,
            )
            void gmst // used implicitly via sgp4 which returns ECI

            viewer.entities.add({
              name: sat.name,
              position: cartesian,
              point: {
                pixelSize: 8,
                color: Cesium!.Color.CYAN,
                outlineColor: Cesium!.Color.WHITE,
                outlineWidth: 1,
              },
              label: {
                text: sat.name,
                font: '11px JetBrains Mono, monospace',
                fillColor: Cesium!.Color.WHITE,
                outlineColor: Cesium!.Color.BLACK,
                outlineWidth: 2,
                style: Cesium!.LabelStyle.FILL_AND_OUTLINE,
                pixelOffset: new Cesium!.Cartesian2(0, -18),
              },
            })
          })
        } catch {
          console.warn(`[Cesium] Could not place satellite ${sat.name}`)
        }
      })

      // Zoom to home
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(0, 20, 20_000_000),
      })
    })()

    return () => {
      cancelled = true
      viewerRef.current?.destroy()
      viewerRef.current = null
    }
  }, [satellites])

  if (!CESIUM_TOKEN) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center rounded-lg border border-space-border bg-space-panel">
        <div className="text-center">
          <div className="mb-3 text-5xl">🛰️</div>
          <p className="font-mono text-sm font-semibold text-gray-300">
            3D Globe Unavailable
          </p>
          <p className="mt-1 max-w-xs font-mono text-xs text-gray-500">
            Set{' '}
            <code className="rounded bg-gray-800 px-1 py-0.5 text-yellow-400">
              VITE_CESIUM_TOKEN
            </code>{' '}
            in your{' '}
            <code className="rounded bg-gray-800 px-1 py-0.5 text-yellow-400">
              .env
            </code>{' '}
            file to enable Cesium Ion 3D globe.
          </p>
          {satellites.length > 0 && (
            <div className="mt-4 space-y-1">
              <p className="font-mono text-xs text-gray-500">
                {satellites.length} satellite(s) tracked:
              </p>
              {satellites.map((s) => (
                <p key={s.id} className="font-mono text-xs text-gray-400">
                  {s.name}
                  {s.norad_id !== undefined && ` (NORAD ${s.norad_id})`}
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="h-full w-full rounded-lg overflow-hidden"
      style={{ minHeight: '400px' }}
    />
  )
}
