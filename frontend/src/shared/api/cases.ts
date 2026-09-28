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
}

export function listCases(query: { keyword?: string; status?: string } = {}) {
  return api.get<CaseRow[]>('/cases', query)
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
