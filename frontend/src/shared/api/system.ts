import { api } from './client'
import type { PageResult } from '../types'

export interface SystemUser {
  id: number
  name: string
  username: string
  mobile?: string | null
  email?: string | null
  department?: string | null
  status: string
  created_at: string
}

export interface SystemRole {
  id: number
  code: string
  name: string
  description?: string | null
  data_scope: string
  status: string
}

export interface SystemDepartment {
  id: number
  name: string
  parent_id?: number | null
  status: string
}

export function listUsers(query: { keyword?: string; page?: number; page_size?: number }) {
  return api.get<PageResult<SystemUser>>('/users', query)
}

export function listRoles() {
  return api.get<SystemRole[]>('/roles')
}

export function listDepartments() {
  return api.get<SystemDepartment[]>('/departments')
}
