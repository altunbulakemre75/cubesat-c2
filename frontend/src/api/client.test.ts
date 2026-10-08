import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import type { AxiosAdapter, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { AxiosError } from 'axios'
import { apiClient, changePassword, logout } from './client'
import { useAppStore } from '../store'

interface Call {
  url: string
  auth: string | undefined
  body: unknown
}

type Handler = (config: InternalAxiosRequestConfig) => { status: number; data: unknown }

// Swap axios' transport for an in-memory router so the real interceptors run.
function routeRequests(handler: Handler): Call[] {
  const calls: Call[] = []
  const adapter: AxiosAdapter = async (config) => {
    calls.push({
      url: config.url ?? '',
      auth: config.headers?.Authorization as string | undefined,
      body: config.data ? JSON.parse(config.data as string) : undefined,
    })
    const { status, data } = handler(config)
    const response: AxiosResponse = {
      data, status, statusText: String(status), headers: {}, config,
    }
    if (status >= 400) {
      throw new AxiosError('fail', String(status), config, null, response)
    }
    return response
  }
  apiClient.defaults.adapter = adapter
  return calls
}

const originalAdapter = apiClient.defaults.adapter

describe('api client — session handling', () => {
  beforeEach(() => {
    useAppStore.getState().clearAuth()
    useAppStore.getState().setAuth('old-access', 'alice')
    useAppStore.getState().setRefreshToken('old-refresh')
  })

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter
  })

  it('changePassword keeps the user logged in with the returned token pair', async () => {
    routeRequests(() => ({
      status: 200,
      data: { access_token: 'new-access', refresh_token: 'new-refresh' },
    }))

    await changePassword('old-password-123', 'new-password-123')

    expect(useAppStore.getState().token).toBe('new-access')
    expect(useAppStore.getState().refreshToken).toBe('new-refresh')
  })

  it('logout sends the refresh token so the server can revoke it too', async () => {
    const calls = routeRequests(() => ({ status: 204, data: null }))

    await logout()

    expect(calls[0].url).toBe('/auth/logout')
    expect(calls[0].body).toEqual({ refresh_token: 'old-refresh' })
    expect(useAppStore.getState().token).toBeNull()
  })

  it('refreshes an expired access token once and retries the request', async () => {
    const calls = routeRequests((config) => {
      if (config.url === '/auth/refresh') {
        return { status: 200, data: { access_token: 'fresh', refresh_token: 'fresh-r' } }
      }
      const auth = config.headers?.Authorization
      return auth === 'Bearer fresh'
        ? { status: 200, data: ['ok'] }
        : { status: 401, data: { detail: 'Invalid or expired token' } }
    })

    const res = await apiClient.get('/satellites')

    expect(res.data).toEqual(['ok'])
    expect(calls.map((c) => c.url)).toEqual(['/satellites', '/auth/refresh', '/satellites'])
    expect(useAppStore.getState().refreshToken).toBe('fresh-r')
  })

  it('concurrent 401s share a single refresh call', async () => {
    // Refresh tokens are single-use: two parallel refreshes would look like
    // a replay to the server and end the whole session.
    const calls = routeRequests((config) => {
      if (config.url === '/auth/refresh') {
        return { status: 200, data: { access_token: 'fresh', refresh_token: 'fresh-r' } }
      }
      return config.headers?.Authorization === 'Bearer fresh'
        ? { status: 200, data: [] }
        : { status: 401, data: {} }
    })

    await Promise.all([apiClient.get('/a'), apiClient.get('/b'), apiClient.get('/c')])

    expect(calls.filter((c) => c.url === '/auth/refresh')).toHaveLength(1)
  })

  it('drops the session when the refresh itself is rejected', async () => {
    routeRequests(() => ({ status: 401, data: {} }))

    await expect(apiClient.get('/satellites')).rejects.toBeInstanceOf(AxiosError)

    expect(useAppStore.getState().token).toBeNull()
  })

  it('does not try to refresh a failed login', async () => {
    const calls = routeRequests(() => ({ status: 401, data: {} }))
    const cfg: AxiosRequestConfig = {}

    await expect(apiClient.post('/auth/login', {}, cfg)).rejects.toBeInstanceOf(AxiosError)

    expect(calls).toHaveLength(1)
  })
})
