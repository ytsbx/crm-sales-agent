import { api } from './client'
import type { PageResult } from '../types'

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

/**
 * 触发一次**回收扫描**（返工单 6.3）。
 *
 * ⚠️ 语义与以前不同：现在只**提名**候选、不改归属 —— 老版本是一次调用就释放一批，
 * 不可逆。回收要等主管在候选列表里批。
 */
export function runPublicPoolRecycle() {
  return api.post<{
    nominated_count: number
    protected_count: number
    disputed_count: number
    already_open_count: number
    notice_days: number
    released_count: number
    candidates: { candidate_id: number; customer_id: number; name: string; level: string }[]
  }>('/public-pool/run-recycle')
}

/** 回收候选（预告）：主管逐条或批量复核的对象。 */
export interface RecycleCandidateRow {
  id: number
  customer_id: number
  customer_name?: string | null
  owner_id?: number | null
  owner_name?: string | null
  level?: string | null
  rule_days?: number | null
  last_contact_at?: string | null
  last_progress_at?: string | null
  last_active_at?: string | null
  /** 提名那一刻的履约保护明细；批准执行前还会再算一次 */
  protection: string[]
  status: string
  status_label: string
  notice_at?: string | null
  due_at?: string | null
  decided_at?: string | null
  decision_note?: string | null
  deferred_until?: string | null
  exception_approved?: boolean
  executed_at?: string | null
  restored_at?: string | null
  restore_note?: string | null
  restore_conflict_owner_id?: number | null
}

export function listRecycleCandidates(query: {
  status?: string
  page?: number
  page_size?: number
} = {}) {
  return api.get<PageResult<RecycleCandidateRow>>('/public-pool/recycle-candidates', query)
}

/** 复核一条候选：approve 执行回收 / reject 驳回 / defer 暂缓。 */
export function decideRecycleCandidate(
  id: number,
  decision: 'approve' | 'reject' | 'defer',
  note?: string,
) {
  return api.post<RecycleCandidateRow>(`/public-pool/recycle-candidates/${id}/decide`, {
    decision,
    note,
  })
}

/** 批量复核。逐条处理、逐条报结果；被拦下的不会被执行。 */
export function batchDecideRecycleCandidates(
  candidateIds: number[],
  decision: 'approve' | 'reject' | 'defer',
  note?: string,
) {
  return api.post<{
    done: { candidate_id: number; status: string }[]
    failed: { candidate_id: number; reason: string; code: number }[]
  }>('/public-pool/recycle-candidates/batch-decide', candidateIds, {
    params: { decision, note },
  })
}

/** 恢复：把被回收的客户还给原负责人（客户已被别人领走时会报 409）。 */
export function restoreRecycleCandidate(id: number, note?: string) {
  return api.post<RecycleCandidateRow>(`/public-pool/recycle-candidates/${id}/restore`, { note })
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
