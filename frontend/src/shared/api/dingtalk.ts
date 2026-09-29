/**
 * 钉钉询价审批（文档 §11.3 :152 / 场景11）。
 *
 * 口径：**发起审批会通知到审批人**（真人），所以这不是"随便试试"的功能。
 * 后端有推送总闸（默认关）：关着时发起只会记一条"未发起"，
 * 不会向钉钉发任何请求，也不会打扰任何人。
 */

import { api } from './client'

export interface OaApprovalInstance {
  id: number
  inquiry_id: number
  inquiry_version: number
  oa_type: string
  instance_id?: string | null
  status: string
  status_label: string
  result?: Record<string, unknown> | null
  error?: string | null
  synced_at?: string | null
  created_at?: string | null
}

/** 该需求历次提交的审批（被驳回的旧轮次也留着，重提不改写历史）。 */
export function listInquiryApprovals(inquiryId: number) {
  return api.get<OaApprovalInstance[]>(`/inquiries/${inquiryId}/oa-approvals`)
}

/**
 * 发起钉钉询价审批。
 *
 * `resubmit=true` 用于**驳回后重提**（新建一轮）；默认同一轮重复点只会复用原记录。
 */
export function startInquiryApproval(inquiryId: number, resubmit = false) {
  return api.post<OaApprovalInstance>(`/inquiries/${inquiryId}/oa-approval`, {
    resubmit,
  })
}

/** 手动拉一次审批状态（与定时任务同一个函数，验收时不用等）。 */
export function syncOaApprovals() {
  return api.post<{ checked: number; changed: number }>('/dingtalk/oa-sync')
}
