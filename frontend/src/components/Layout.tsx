import { NavLink, Outlet } from 'react-router-dom'
import { clsx } from 'clsx'
import { useEventsWS } from '../hooks/useEventsWS'
import { useAppStore } from '../store'

interface NavItem {
  to: string
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: '◉' },
  { to: '/commands', label: 'Commands', icon: '⌨' },
  { to: '/passes', label: 'Pass Schedule', icon: '◷' },
  { to: '/users', label: 'Users', icon: '◉' },
]

function SidebarLink({ to, label, icon }: NavItem) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        clsx(
          'flex items-center gap-3 rounded-lg px-3 py-2.5 font-mono text-sm transition-colors',
          isActive
            ? 'bg-space-accent/20 text-space-accent'
            : 'text-gray-400 hover:bg-space-border hover:text-white',
        )
      }
    >
      <span className="text-base">{icon}</span>
      {label}
    </NavLink>
  )
}

export function Layout() {
  // Connect to global events WebSocket
  useEventsWS()

  const activeAlertCount = useAppStore((s) => s.activeAlerts.length)

  return (
    <div className="flex h-screen w-full overflow-hidden bg-space-dark">
      {/* Sidebar */}
      <aside className="flex w-56 flex-shrink-0 flex-col border-r border-space-border bg-space-panel">
        {/* Logo */}
        <div className="border-b border-space-border p-4">
          <div className="flex items-center gap-2">
            <span className="text-xl">🛰️</span>
            <div>
              <p className="font-mono text-sm font-bold text-white">CubeSat C2</p>
              <p className="font-mono text-xs text-gray-500">Command &amp; Control</p>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => (
            <SidebarLink key={item.to} {...item} />
          ))}
        </nav>

        {/* Status footer */}
        <div className="border-t border-space-border p-3">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-green-500" />
            <span className="font-mono text-xs text-gray-500">System Online</span>
          </div>
          {activeAlertCount > 0 && (
            <div className="mt-1 flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
              <span className="font-mono text-xs text-red-400">
                {activeAlertCount} active alert{activeAlertCount !== 1 ? 's' : ''}
              </span>
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
