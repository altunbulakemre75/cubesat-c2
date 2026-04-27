import React, { useEffect, useRef } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { SatelliteDetail } from './pages/SatelliteDetail'
import { CommandCenter } from './pages/CommandCenter'
import { PassSchedule } from './pages/PassSchedule'
import { Login } from './pages/Login'
import { ChangePassword } from './pages/ChangePassword'
import { UserManagement } from './pages/UserManagement'
import { login } from './api/client'
import { useAppStore } from './store'

// Dev-only auto-login. Production builds (`vite build`) flip
// import.meta.env.DEV to false, so this entire block is dead-stripped from
// the bundle and the login screen comes back. To turn it off in dev too,
// set VITE_DISABLE_AUTO_LOGIN=1 in .env.
function useDevAutoLogin(): void {
  const token = useAppStore((s) => s.token)
  const attemptedRef = useRef(false)

  useEffect(() => {
    if (!import.meta.env.DEV) return
    if (import.meta.env.VITE_DISABLE_AUTO_LOGIN) return
    if (token) return
    if (attemptedRef.current) return
    attemptedRef.current = true
    login('admin', 'admin').catch((err) => {
      console.warn('[dev auto-login] failed — fall back to /login', err)
    })
  }, [token])
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: false },
  },
})

// ── Error Boundary ───────────────────────────────────────────────────────────

interface EBState { error: Error | null }

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, EBState> {
  state: EBState = { error: null }

  static getDerivedStateFromError(error: Error): EBState {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen items-center justify-center bg-gray-950 p-8">
          <div className="max-w-lg rounded-lg border border-red-900 bg-gray-900 p-6">
            <p className="font-mono text-sm font-bold text-red-400">Runtime Error</p>
            <p className="mt-2 font-mono text-xs text-gray-300">
              {this.state.error.message}
            </p>
            <pre className="mt-3 max-h-48 overflow-auto rounded bg-gray-800 p-3 font-mono text-xs text-gray-500">
              {this.state.error.stack}
            </pre>
            <button
              className="mt-4 rounded bg-blue-700 px-4 py-2 font-mono text-xs text-white hover:bg-blue-600"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Auth guard ───────────────────────────────────────────────────────────────

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAppStore((s) => s.token)
  useDevAutoLogin()
  if (!token) {
    // In dev mode, useDevAutoLogin is racing to set the token. Render a
    // brief placeholder instead of bouncing to /login — saves a flash.
    if (import.meta.env.DEV && !import.meta.env.VITE_DISABLE_AUTO_LOGIN) {
      return (
        <div className="flex h-screen items-center justify-center bg-gray-950 font-mono text-xs text-gray-500">
          Auto-logging in (dev mode)…
        </div>
      )
    }
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

// ── App ──────────────────────────────────────────────────────────────────────

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
          <Route
            path="/change-password"
            element={
              <RequireAuth>
                <ChangePassword />
              </RequireAuth>
            }
          />
            <Route
              element={
                <RequireAuth>
                  <ErrorBoundary>
                    <Layout />
                  </ErrorBoundary>
                </RequireAuth>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="satellites/:id" element={<SatelliteDetail />} />
              <Route path="commands" element={<CommandCenter />} />
              <Route path="passes" element={<PassSchedule />} />
              <Route path="users" element={<UserManagement />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
