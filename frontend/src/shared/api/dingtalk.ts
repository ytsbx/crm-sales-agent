/**
 * 钉钉询价审批（文档 §11.3 :152 / 场景11）。
 *
 * 口径：**发起审批会通知到审批人**（真人），所以这不是"随便试试"的功能。
 * 后端有推送总闸（默认关）：关着时发起只会记一条"未发起"，
 * 不会向钉钉发任何请求，也不会打扰任何人。
 */

import { api } from './client'

/**
 * 服务端给的「状态 → 允许动作」清单（第七批 7.7）。
 *
 * **按钮必须按它渲染，不要在前端再写一套 if 判断状态**：后端和前端各写一套
 * 必然分叉，分叉出去的那一侧就是"待审批/已通过还能再点一次重提"，
 * 而那会在钉钉里另建一张审批单（重复外部实例）。
 */
export type OaAction =
  | 'sync'
  | 'retry'
  | 'resubmit'
  | 'resolve_adopt'
  | 'resolve_resend'
  | 'resolve_abandon'

export interface OaApprovalInstance {
  id: number
  inquiry_id: number
  inquiry_version: number
  oa_type: string
  instance_id?: string | null
  status: string
  status_label: string
  /** 请求号（幂等键）：出问题时拿它和钉钉那一次请求对账 */
  request_no?: string | null
  submit_round?: number
  /** 这一轮向钉钉发起过几次（重试会累加；轮次看 submit_round） */
  attempt_count?: number
  result?: Record<string, unknown> | null
  error?: string | null
  /** 核定占用：processing 表示有人正在对这条记录做外部动作 */
  resolve_state?: string | null
  resolve_state_label?: string | null
  resolved_at?: string | null
  resolved_action?: string | null
  /** 当前允许的动作（服务端口径） */
  allowed_actions?: OaAction[]
  synced_at?: string | null
  created_at?: string | null
  last_attempt_at?: string | null
}

export interface OaResolvePayload {
  action: 'adopt' | 'resend' | 'abandon'
  instance_id?: string
  note?: string
  /**
   * 同一次核定的请求键：服务端用它保证"这次核定重发只生效一次，并回放同一份结果"。
   * **每次打开核定对话框生成一个**，同一个对话框里的重试复用同一个键——
   * 重试时换新键等于把"重试"变成"又一次核定"。
   */
  request_key?: string
}

/** 该需求历次提交的审批（被驳回的旧轮次也留着，重提不改写历史）。 */
export function listInquiryApprovals(inquiryId: number) {
  return api.get<OaApprovalInstance[]>(`/inquiries/${inquiryId}/oa-approvals`)
}

/**
 * 发起钉钉询价审批。
 *
 * `resubmit=true` 用于**驳回后重提**（新建一轮）；默认同一轮重复点只会复用原记录，
 * 而 `failed` / `not_sent` / `skipped` 会**同轮重新发起**（外部确定没建单，重试安全）。
 * 待审批、已通过、结果未知时后端会拒绝重提（`allowed_actions` 里没有 `resubmit`）。
 */
export function startInquiryApproval(inquiryId: number, resubmit = false) {
  return api.post<OaApprovalInstance>(`/inquiries/${inquiryId}/oa-approval`, {
    resubmit,
  })
}

/**
 * 人工处理"结果不明"的发起（口径 2026-10-04：不自动重发，转人工）。
 *
 * - adopt：钉钉已建单 → 填实例号接过来（服务端会先核实模板/发起人/来源需求）；
 * - resend：确认没建 → 复用同一轮重新发起；
 * - abandon：确认不发了 → 作废本轮。
 */
export function resolveOaInstance(oaId: number, payload: OaResolvePayload) {
  return api.post<OaApprovalInstance>(`/oa-instances/${oaId}/resolve`, payload)
}

/** 手动拉一次审批状态（与定时任务同一个函数，验收时不用等）。 */
export function syncOaApprovals() {
  return api.post<{ checked: number; changed: number; disabled?: boolean; message?: string }>('/dingtalk/oa-sync')
}
