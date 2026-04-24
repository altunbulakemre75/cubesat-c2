import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { changeUserRole, createUser, fetchUsers, type User } from '../api/users'

type Role = User['role']

export function UserManagement() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [formError, setFormError] = useState('')

  const usersQ = useQuery({
    queryKey: ['users'],
    queryFn: fetchUsers,
  })

  const createMut = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      setUsername(''); setEmail(''); setPassword(''); setRole('viewer')
      setShowForm(false); setFormError('')
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Kullanıcı oluşturulamadı'
      setFormError(msg)
    },
  })

  const roleMut = useMutation({
    mutationFn: (args: { username: string; role: Role }) => changeUserRole(args.username, args.role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setFormError('')
    if (password.length < 12) {
      setFormError('Password must be at least 12 characters')
      return
    }
    createMut.mutate({ username, email, password, role })
  }

  return (
    <div className="flex h-full flex-col overflow-auto">
      <div className="flex items-center justify-between border-b border-space-border p-4">
        <div>
          <h1 className="font-mono text-lg font-bold text-white">User Management</h1>
          <p className="font-mono text-xs text-gray-500">Admin only · JWT + RBAC</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded border border-space-accent bg-space-accent/10 px-3 py-1.5 font-mono text-xs text-space-accent hover:bg-space-accent hover:text-white"
        >
          {showForm ? 'Cancel' : '+ New User'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit}
              className="border-b border-space-border bg-space-panel p-4">
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">Username</label>
              <input required value={username} onChange={(e) => setUsername(e.target.value)}
                     className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">Email</label>
              <input required type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">Password</label>
              <input required type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                     className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-blue-500"
                     placeholder="min 12 chars" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-blue-500">
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </div>
          </div>
          {formError && <p className="mt-2 text-red-400 text-xs font-mono">{formError}</p>}
          <div className="mt-3 flex justify-end">
            <button type="submit" disabled={createMut.isPending}
                    className="rounded bg-blue-600 px-4 py-1.5 font-mono text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50">
              {createMut.isPending ? 'Ekleniyor...' : 'Create User'}
            </button>
          </div>
        </form>
      )}

      <div className="flex-1 p-4">
        {usersQ.isLoading && <p className="text-xs text-gray-500 font-mono">Yükleniyor...</p>}
        {usersQ.isError && <p className="text-xs text-red-400 font-mono">
          Kullanıcı listesi alınamadı. Admin rolüne sahip olduğundan emin misin?
        </p>}
        {usersQ.data && (
          <table className="w-full font-mono text-sm">
            <thead>
              <tr className="border-b border-space-border text-xs text-gray-500 uppercase tracking-wider">
                <th className="py-2 text-left">Username</th>
                <th className="py-2 text-left">Email</th>
                <th className="py-2 text-left">Role</th>
                <th className="py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {usersQ.data.map((u) => (
                <tr key={u.id} className="border-b border-space-border/50">
                  <td className="py-2 text-white">{u.username}</td>
                  <td className="py-2 text-gray-400">{u.email}</td>
                  <td className="py-2">
                    <select value={u.role}
                            onChange={(e) => roleMut.mutate({ username: u.username, role: e.target.value as Role })}
                            className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white">
                      <option value="viewer">viewer</option>
                      <option value="operator">operator</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="py-2">
                    {u.active ? (
                      <span className="text-green-400 text-xs">● active</span>
                    ) : (
                      <span className="text-gray-500 text-xs">○ disabled</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
