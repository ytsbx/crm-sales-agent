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
  sync_notice?: string | null
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
export function startInquiryApproval(inquiryId: number, resubmit = false, quoteOwnerUserId?: string, extraFields?: Record<string, string>, attachmentFileIds?: number[]) {
  return api.post<OaApprovalInstance>(`/inquiries/${inquiryId}/oa-approval`, {
    resubmit,
    ...(quoteOwnerUserId ? { quote_owner_user_id: quoteOwnerUserId } : {}),
    ...(extraFields ? { extra_fields: extraFields } : {}),
    ...(attachmentFileIds ? { attachment_file_ids: attachmentFileIds } : {}),
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

/** 撤回仍在钉钉流转中的询价或独立打样审批。 */
export function withdrawOaInstance(oaId: number) {
  return api.post<OaApprovalInstance>(`/oa-instances/${oaId}/withdraw`, {})
}

export function syncOaInstance(oaId: number) {
  return api.post<OaApprovalInstance>(`/oa-instances/${oaId}/sync`, {})
}

/** 手动拉一次审批状态（与定时任务同一个函数，验收时不用等）。 */
export function syncOaApprovals() {
  return api.post<{ checked: number; changed: number; disabled?: boolean; message?: string }>('/dingtalk/oa-sync')
}

/**
 * 同步成员 —— 把 CRM 账号和钉钉通讯录里的人对上（写账号上的「钉钉编号」）。
 *
 * 这是**钉钉登录的地基**：钉钉只会告诉系统"这是某某的钉钉号"，它不知道这人在
 * CRM 里是什么角色；对上之后系统才认得出"进来的这个人是哪一位"。
 *
 * 服务端**不建账号**：钉钉里有人、CRM 里没账号的，只会出现在 `unmatched` 里，
 * 由管理员去建。已经绑好的也不会被改绑（改绑等于换掉一个人的登录身份）。
 */
export function syncDingTalkUsers() {
  return api.post<DingTalkSyncResult>('/dingtalk/sync-users')
}

/** 对不上的一条。`reason` 是机器判据，`reason_label` 是直接能显示的说明。 */
export interface DingTalkUnmatched {
  name: string
  mobile: string
  reason: 'no_account' | 'ambiguous_mobile' | 'ambiguous_name'
  reason_label: string
}

export interface DingTalkSyncResult {
  /**
   * 推送总闸关着。此时**一次请求都没发出去**，下面的数字全是 0，
   * 原因在 `message` 里 —— 界面必须把它和"钉钉里确实没人"区分开。
   */
  disabled: boolean
  message?: string
  /** 钉钉通讯录里一共多少人 */
  total: number
  /** 本次新绑上的 */
  bound: number
  /** 本次开始前就已经绑好的 */
  already_bound: number
  /** 对不上的（要管理员去处理，不是错误） */
  unmatched: DingTalkUnmatched[]
  /** CRM 里还没绑钉钉的在用账号（这些人暂时登不了钉钉） */
  unbound_crm: { id: number; name: string; username: string }[]
}


export interface InquirySubmissionOptions {
  configured: boolean
  enabled: boolean
  quote_owner_required: boolean
  quote_owners: Array<{ user_id: string; name: string }>
  selected_quote_owner?: string | null
  image_required: boolean
  file_business_type: string
  fields: Array<{ id: string; label: string; type: string; required: boolean; options: string[] }>
}
export function getInquirySubmissionOptions(inquiryId?: number) {
  return api.get<InquirySubmissionOptions>(inquiryId ? `/inquiries/${inquiryId}/oa-submission` : '/inquiries/oa-submission-options')
}
