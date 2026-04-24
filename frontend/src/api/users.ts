import { apiClient } from './client'

export interface User {
  id: string
  username: string
  email: string
  role: 'viewer' | 'operator' | 'admin'
  active: boolean
}

export interface CreateUserInput {
  username: string
  email: string
  password: string
  role: 'viewer' | 'operator' | 'admin'
}

export async function fetchUsers(): Promise<User[]> {
  const res = await apiClient.get<User[]>('/users')
  return res.data
}

export async function createUser(input: CreateUserInput): Promise<User> {
  const res = await apiClient.post<User>('/users', input)
  return res.data
}

export async function changeUserRole(username: string, role: User['role']): Promise<void> {
  await apiClient.patch(`/users/${username}/role`, { role })
}
