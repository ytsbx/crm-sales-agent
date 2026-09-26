import { api } from './client'
import type { PageResult } from '../types'

/** 用户 / 部门 / 角色（03-API §3 / §4 / §5）。 */

export interface SystemUser {
  id: number
  name: string
  username: string
  mobile?: string | null
  email?: string | null
  department_id?: number | null
  department?: string | null
  wecom_userid?: string | null
  status: string
  roles?: { id: number; code: string; name: string; data_scope: string }[]
  created_at?: string
}

export interface SystemRole {
  id: number
  code: string
  name: string
  description?: string | null
  data_scope: string
  status: string
  permission_codes?: string[]
  users?: number
}

export interface SystemDepartment {
  id: number
  name: string
  parent_id?: number | null
  wecom_department_id?: string | null
  status: string
  /** 下级部门数量（详情接口返回）。注意与树接口的 children 数组区分。 */
  child_count?: number
  users?: number
}

export interface SystemPermission {
  id: number
  code: string
  name: string
  resource?: string | null
  action?: string | null
}

export interface UserDataScope {
  user_id: number
  department_id?: number | null
  department?: string | null
  data_scope: string
  roles: { code: string; name: string; data_scope: string }[]
}

export interface DepartmentTreeNode extends SystemDepartment {
  children: DepartmentTreeNode[]
}

export function listUsers(query: {
  keyword?: string
  status?: string
  department_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<SystemUser>>('/users', query)
}

export function getUser(id: number) {
  return api.get<SystemUser>(`/users/${id}`)
}

export function createUser(payload: Record<string, unknown>) {
  return api.post<SystemUser>('/users', payload)
}

export function updateUser(id: number, payload: Record<string, unknown>) {
  return api.patch<SystemUser>(`/users/${id}`, payload)
}

export function enableUser(id: number) {
  return api.post<SystemUser>(`/users/${id}/enable`)
}

export function disableUser(id: number) {
  return api.post<SystemUser>(`/users/${id}/disable`)
}

export function getUserRoles(id: number) {
  return api.get<SystemUser['roles']>(`/users/${id}/roles`)
}

export function setUserRoles(id: number, roleIds: number[]) {
  return api.put<SystemUser['roles']>(`/users/${id}/roles`, { role_ids: roleIds })
}

export function getUserDataScope(id: number) {
  return api.get<UserDataScope>(`/users/${id}/data-scope`)
}

export function listRoles() {
  return api.get<SystemRole[]>('/roles')
}

export function getRole(id: number) {
  return api.get<SystemRole>(`/roles/${id}`)
}

export function createRole(payload: Record<string, unknown>) {
  return api.post<SystemRole>('/roles', payload)
}

export function updateRole(id: number, payload: Record<string, unknown>) {
  return api.patch<SystemRole>(`/roles/${id}`, payload)
}

export function deleteRole(id: number) {
  return api.delete<null>(`/roles/${id}`)
}

export function listPermissions() {
  return api.get<SystemPermission[]>('/permissions')
}

export function listDepartments() {
  return api.get<SystemDepartment[]>('/departments')
}

export function getDepartmentTree() {
  return api.get<DepartmentTreeNode[]>('/departments/tree')
}

export function getDepartment(id: number) {
  return api.get<SystemDepartment>(`/departments/${id}`)
}

export function createDepartment(payload: Record<string, unknown>) {
  return api.post<SystemDepartment>('/departments', payload)
}

export function updateDepartment(id: number, payload: Record<string, unknown>) {
  return api.patch<SystemDepartment>(`/departments/${id}`, payload)
}

export function deleteDepartment(id: number) {
  return api.delete<null>(`/departments/${id}`)
}

export function listDepartmentUsers(id: number) {
  return api.get<SystemUser[]>(`/departments/${id}/users`)
}
