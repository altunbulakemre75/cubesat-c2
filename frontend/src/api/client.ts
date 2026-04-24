import axios, { type AxiosError, type AxiosResponse } from 'axios'
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

// Response interceptor — normalise errors
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
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

export async function login(username: string, password: string): Promise<void> {
  const res = await apiClient.post<{ access_token: string }>('/auth/login', { username, password })
  useAppStore.getState().setAuth(res.data.access_token, username)
}

export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8000'
