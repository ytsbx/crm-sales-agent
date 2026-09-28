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
