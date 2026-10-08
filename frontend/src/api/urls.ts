// The UI reaches the backend through its own origin: nginx (production) and
// the Vite dev server (development) proxy /api and /ws to the backend. That
// way the backend port never needs to be exposed and the app works from any
// machine on the network, not only from the docker host's localhost.

export function sameOriginWsBase(loc: { protocol: string; host: string }): string {
  return `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}`
}

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const WS_BASE_URL: string =
  import.meta.env.VITE_WS_BASE_URL ?? sameOriginWsBase(window.location)
