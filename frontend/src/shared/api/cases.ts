import type { PageResult } from '../types'
import { api } from './client'

export interface CaseRow {
  id: number
  title: string
  author_id: number
  author_name: string | null
  customer_id: number | null
  customer_name: string | null
  customer_label: string | null
  industry: string | null
  product_line: string | null
  stage_reached: string | null
  problem_tags: string[] | null
  background: string | null
  goal: string | null
  key_actions: string | null
  objection_handling: string | null
  process: string | null
  result: string | null
  lessons: string | null
  // 证据单据引用（§3.7：从已有时间线和单据选证据）
  quote_id: number | null
  order_id: number | null
  sample_id: number | null
  opportunity_id: number | null
  status: string
  status_label: string
  reviewer_name: string | null
  review_note: string | null
  created_at: string
  /**
   * 分享版脱敏（§3.7）：抹掉了哪几类敏感片段、各几处。
   * 作者/主管视角也会返回——发布前据此自查正文，别等读者看到一堆〔金额〕。
   */
  redaction_summary?: string[]
  /** 关联单据中，当前账号没有查看权限、因此未下发 id 的那几个。 */
  hidden_evidence?: string[]
  /** 当前返回的是否为分享版（客户身份+正文均已脱敏）。 */
  share_view?: boolean
  /** 第几版（§5.1.5）。修订稿从 V2 起。 */
  version?: number
  /** 这一版是从哪一版改出来的（null = 原版） */
  revision_of_id?: number | null
  /**
   * 这一版已经被谁取代了（有值 = 只读历史版本）。
   * 注意"已被取代"不等于"看不到"：旧版照常能读、能搜到，
   * 只是不能再改——要改就基于它再开一份修订稿。
   */
  superseded_by?: number | null
  /** 逐条审核历史（每一轮的意见都留着，不会被下一次审核覆盖） */
  review_history?: { approve?: boolean; note?: string | null; reviewer_name?: string | null; at?: string | null }[]
}

/**
 * 案例列表（第四批 §5.1.6 改成真分页）。
 *
 * `industry` / `product_line` / `stage` 这三个筛选后端**一直就支持**，
 * 之前只是前端没接通——列表看着"只能按关键词和状态搜"。
 * `include_history` 为 true 时才列出被修订版取代的历史版本。
 */
export function listCases(
  query: {
    keyword?: string
    status?: string
    industry?: string
    product_line?: string
    stage?: string
    include_history?: boolean
    page?: number
    page_size?: number
  } = {},
) {
  return api.get<PageResult<CaseRow>>('/cases', query)
}

export function getCase(id: number) {
  return api.get<CaseRow>(`/cases/${id}`)
}

export function createCase(payload: Record<string, unknown>) {
  return api.post<CaseRow>('/cases', payload)
}

export function updateCase(id: number, payload: Record<string, unknown>) {
  return api.patch<CaseRow>(`/cases/${id}`, payload)
}

export function submitCase(id: number) {
  return api.post<CaseRow>(`/cases/${id}/submit`)
}

export function reviewCase(id: number, payload: { approve: boolean; note?: string | null }) {
  return api.post<CaseRow>(`/cases/${id}/review`, payload)
}

/**
 * 从已发布（或已被取代）的案例开一份**修订稿**（§5.1.5）。
 *
 * 为什么不能直接在原案例上改：审核批的是"这一版内容"，改完还挂着"已发布"，
 * 等于复用了一个对不上号的审核结论。所以已发布的那一版**只读**，
 * 要改就复制一份新的重新走审核；批准后新版替换当前发布版，
 * 原版转为"已被取代"——但仍然**读得到**（培训资料不断档）。
 *
 * 后端做了幂等：同一原版最多一份在途修订稿。重复点不会建出一堆 V2。
 */
export function reviseCase(id: number) {
  return api.post<CaseRow>(`/cases/${id}/revise`)
}
