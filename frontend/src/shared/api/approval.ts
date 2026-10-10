import { api } from './client'

// ---------------------------------------------------------------- 审批流转规则（设计稿 `_6` 的国内业务版）
//
// 生效模型：草稿（conditions/action 的编辑）→ 发布（生成版本快照）→ 引擎只按已发布版本求值；
// `enabled` 是立即生效的启停开关；沙盒对当前草稿试算，用于"发布前先验证"。

export interface RuleCondition {
  field: string
  op: string
  value: unknown
}

export interface ApprovalRuleRow {
  id: number
  name: string
  kind: 'auto_pass' | 'express' | 'exception_route'
  priority: number
  enabled: boolean
  conditions: RuleCondition[]
  action: Record<string, unknown>
  description?: string | null
  published_version_no: number
  published_at?: string | null
  updated_at: string
  effect: string
  conditions_pretty: Array<{ label: string; value_label: string; hit?: boolean }>
  has_draft_changes?: boolean
}

export interface ConditionFieldMeta {
  field: string
  label: string
  value_type: 'number' | 'string' | 'bool'
  unit: string
  ops: string[]
  hint: string
}

export interface RuleVersionRow {
  id: number
  version_no: number
  payload: { name: string; kind: string; priority: number; conditions: RuleCondition[]; action: Record<string, unknown> }
  published_by?: number | null
  published_at: string
  is_current: boolean
}

export interface SandboxConditionDetail {
  field: string
  label: string
  op: string
  value: unknown
  actual: unknown
  hit: boolean
  value_label?: string
  actual_label?: string
  note?: string
}

export interface SandboxResult {
  context: Record<string, unknown>
  context_fields: ConditionFieldMeta[]
  rules: Array<{
    rule_id: number
    name: string
    kind: string
    priority: number
    enabled: boolean
    published_version_no: number
    matched: boolean
    fired: boolean
    effect: string | null
    conditions_detail: SandboxConditionDetail[]
  }>
  fired_rule_id: number | null
  fired_effect: string | null
}

export const RULE_KIND_LABEL: Record<string, string> = {
  auto_pass: '免审',
  express: '极速通道',
  exception_route: '异常加签',
}

export function listApprovalRules() {
  return api.get<ApprovalRuleRow[]>('/approval-rules')
}

export function listConditionFields() {
  return api.get<ConditionFieldMeta[]>('/approval-rules/condition-fields')
}

export function createApprovalRule(payload: {
  name: string
  kind: string
  priority?: number
  conditions: RuleCondition[]
  action?: Record<string, unknown>
  description?: string | null
}) {
  return api.post<ApprovalRuleRow>('/approval-rules', payload)
}

export function updateApprovalRule(
  id: number,
  payload: {
    name: string
    kind: string
    priority?: number
    conditions: RuleCondition[]
    action?: Record<string, unknown>
    description?: string | null
  },
) {
  return api.patch<ApprovalRuleRow>(`/approval-rules/${id}`, payload)
}

export function toggleApprovalRule(id: number, enabled: boolean) {
  return api.patch<ApprovalRuleRow>(`/approval-rules/${id}/enabled`, { enabled })
}

/**
 * 发布审批规则。
 *
 * ⚠️ 发布之后**后续提交的报价审批都按新规则算**（改的是"哪些报价要审批、
 * 走哪一级"）。后端要求 `confirm=true`（不带返回 42206 + 规则条件说明）。
 */
export function publishApprovalRule(id: number, confirm = false) {
  return api.post<ApprovalRuleRow>(
    `/approval-rules/${id}/publish${confirm ? '?confirm=true' : ''}`,
  )
}

export function deleteApprovalRule(id: number) {
  return api.delete<null>(`/approval-rules/${id}`)
}

export function listApprovalRuleVersions(id: number) {
  return api.get<RuleVersionRow[]>(`/approval-rules/${id}/versions`)
}

export function runApprovalSandbox(quoteVersionId: number) {
  return api.post<SandboxResult>('/approval-rules/sandbox', { quote_version_id: quoteVersionId })
}
