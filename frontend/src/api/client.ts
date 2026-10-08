import axios, { type AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { useAppStore } from '../store'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 15_000,
})

// Attach JWT from in-memory store on every request
apiClient.interceptors.request.use(
  (config) => {
    const token = useAppStore.getState().token
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  },
  (error: AxiosError) => Promise.reject(error),
)

// Endpoints whose 401 means "bad credentials", not "access token expired".
const NO_REFRESH_URLS = new Set(['/auth/login', '/auth/refresh', '/auth/logout'])

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retriedAfterRefresh?: boolean
}

// Response interceptor — transparently refresh an expired access token once,
// then normalise errors
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    if (
      error.response?.status === 401 &&
      config &&
      !config._retriedAfterRefresh &&
      !NO_REFRESH_URLS.has(config.url ?? '')
    ) {
      config._retriedAfterRefresh = true
      if (await refreshAccessToken()) {
        config.headers.Authorization = `Bearer ${useAppStore.getState().token}`
        return apiClient(config)
      }
    }

    if (error.response) {
      const status = error.response.status
      if (status === 401) console.warn('[API] Unauthorized (401)')
      else if (status === 403) console.warn('[API] Forbidden (403)')
      else if (status >= 500) console.error('[API] Server error', error.response.data)
    } else if (error.request) {
      console.error('[API] No response received — backend may be down')
    }
    return Promise.reject(error)
  },
)

export interface LoginResult {
  mustChangePassword: boolean
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await apiClient.post<{
    access_token: string
    refresh_token: string | null
    must_change_password: boolean
  }>('/auth/login', { username, password })
  useAppStore.getState().setAuth(res.data.access_token, username)
  if (res.data.refresh_token) {
    useAppStore.getState().setRefreshToken(res.data.refresh_token)
  }
  return { mustChangePassword: res.data.must_change_password }
}

interface TokenPair {
  access_token: string
  refresh_token: string
}

// The server ends every session on password change and hands back a fresh
// pair for this client, so the user stays logged in here only.
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const res = await apiClient.post<TokenPair>('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
  const username = useAppStore.getState().username ?? ''
  useAppStore.getState().setAuth(res.data.access_token, username)
  useAppStore.getState().setRefreshToken(res.data.refresh_token)
}

export async function logout(): Promise<void> {
  const refreshToken = useAppStore.getState().refreshToken
  try { await apiClient.post('/auth/logout', { refresh_token: refreshToken }) }
  catch { /* even if revocation fails on the server, drop local state */ }
  useAppStore.getState().clearAuth()
}

// Refresh tokens are single-use: the server treats a second use as a stolen
// copy and ends the whole session. Parallel 401s must therefore share one
// in-flight refresh instead of each starting their own.
let refreshInFlight: Promise<boolean> | null = null

export function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= doRefresh().finally(() => { refreshInFlight = null })
  return refreshInFlight
}

async function doRefresh(): Promise<boolean> {
  const refreshToken = useAppStore.getState().refreshToken
  if (!refreshToken) return false
  try {
    const res = await apiClient.post<{ access_token: string; refresh_token: string }>(
      '/auth/refresh',
      { refresh_token: refreshToken },
    )
    const username = useAppStore.getState().username ?? ''
    useAppStore.getState().setAuth(res.data.access_token, username)
    useAppStore.getState().setRefreshToken(res.data.refresh_token)
    return true
  } catch {
    useAppStore.getState().clearAuth()
    return false
  }
}

export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000'
