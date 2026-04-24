/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CESIUM_TOKEN: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_WS_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
