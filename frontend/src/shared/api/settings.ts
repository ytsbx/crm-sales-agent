import { api } from './client'

export interface SystemSettingRow {
  id: number
  key: string
  value?: Record<string, unknown> | null
  description?: string | null
  updated_at: string
}

export interface PublicPoolRuleRow {
  id: number
  level: string
  days: number
  enabled: boolean
  remark?: string | null
}

export interface TaskRuleRow {
  id: number
  code: string
  name: string
  trigger_type: string
  trigger_config?: Record<string, unknown> | null
  action_config?: Record<string, unknown> | null
  status: string
}

export function listSettings() {
  return api.get<SystemSettingRow[]>('/settings')
}

/** 所有登录用户可读的少量配置：决定界面显示什么 */
export function getPublicConfig() {
  return api.get<{ trade_mode: string; default_currency: string }>('/meta/config')
}

export function saveSetting(key: string, value: Record<string, unknown>, description?: string) {
  return api.patch<SystemSettingRow>('/settings', { key, value, description })
}

export function listPublicPoolRules() {
  return api.get<PublicPoolRuleRow[]>('/public-pool/rules')
}

export function updatePublicPoolRule(id: number, payload: Record<string, unknown>) {
  return api.patch<PublicPoolRuleRow>(`/public-pool/rules/${id}`, payload)
}

export function runPublicPoolRecycle() {
  return api.post<{ released_count: number }>('/public-pool/run-recycle')
}

export function listTaskRules() {
  return api.get<TaskRuleRow[]>('/task-rules')
}

export function updateTaskRule(id: number, payload: Record<string, unknown>) {
  return api.patch<TaskRuleRow>(`/task-rules/${id}`, payload)
}

export function runAutoTaskRules() {
  return api.post<{ created_count: number; failed_rule_count?: number; rule_errors?: { rule_id: number; code: string; error: string }[] }>('/tasks/run-auto-rules')
}
