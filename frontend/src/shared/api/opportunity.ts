import { api } from './client'
import type { PageResult } from '../types'

export interface Opportunity {
  id: number
  customer_id: number
  customer_name?: string | null
  primary_contact_id?: number | null
  title: string
  source?: string | null
  stage_id: number
  stage_name?: string | null
  stage_code?: string | null
  expected_amount?: number | null
  currency: string
  expected_close_date?: string | null
  owner_id?: number | null
  owner_name?: string | null
  competitor?: string | null
  risk_level?: string | null
  next_action?: string | null
  status: string
  loss_reason_id?: number | null
  loss_reason_name?: string | null
  loss_remark?: string | null
  reopen_at?: string | null
  item_count: number
  created_at: string
  updated_at: string
}

export interface OpportunityItem {
  id: number
  opportunity_id: number
  sku_id: number
  sku_code?: string | null
  sku_name?: string | null
  product_id?: number | null
  specification?: string | null
  color?: string | null
  quantity: number
  target_price?: number | null
  currency: string
  package_requirement?: string | null
  delivery_date?: string | null
  destination?: string | null
  remark?: string | null
  moq?: number | null
  unit?: string | null
}

export interface Stage {
  id: number
  code: string
  name: string
  sequence: number
  is_win: boolean
  is_loss: boolean
  status: string
}

export interface LossReason {
  id: number
  code: string
  name: string
  category?: string | null
}

export interface FunnelRow {
  stage_id: number
  stage_name: string
  sequence: number
  count: number
  amount: number
}

export function listOpportunities(query: {
  keyword?: string
  stage_id?: number
  status?: string
  owner_id?: number
  customer_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<Opportunity>>('/opportunities', query)
}

export function getOpportunity(id: number) {
  return api.get<Opportunity>(`/opportunities/${id}`)
}

export function createOpportunity(payload: Record<string, unknown>) {
  return api.post<Opportunity>('/opportunities', payload)
}

export function updateOpportunity(id: number, payload: Record<string, unknown>) {
  return api.patch<Opportunity>(`/opportunities/${id}`, payload)
}

export function changeStage(id: number, payload: { stage_id?: number; stage_code?: string; remark?: string }) {
  return api.post<Opportunity>(`/opportunities/${id}/change-stage`, payload)
}

export function winOpportunity(id: number, payload: { remark?: string } = {}) {
  return api.post<Opportunity>(`/opportunities/${id}/win`, payload)
}

/** 确认成交并生成订单（方案 §5 / A13）：接受→成交→建单一次完成，重试幂等。 */
export function confirmWin(id: number, payload: { win_quote_version_id?: number; delivery_date?: string | null; remark?: string } = {}) {
  return api.post<{
    opportunity_id: number
    quote_id: number
    win_quote_version_id: number
    order_id: number
    order_no: string
    already_won: boolean
    already_accepted: boolean
    already_ordered: boolean
  }>(`/opportunities/${id}/confirm-win`, payload)
}

export function loseOpportunity(id: number, payload: { loss_reason_id: number; remark?: string; reopen_at?: string }) {
  return api.post<Opportunity>(`/opportunities/${id}/lose`, payload)
}

export function reopenOpportunity(id: number) {
  return api.post<Opportunity>(`/opportunities/${id}/reopen`)
}

export function listItems(opportunityId: number) {
  return api.get<OpportunityItem[]>(`/opportunities/${opportunityId}/items`)
}

export function createItem(opportunityId: number, payload: Record<string, unknown>) {
  return api.post<OpportunityItem>(`/opportunities/${opportunityId}/items`, payload)
}

export function updateItem(itemId: number, payload: Record<string, unknown>) {
  return api.patch<OpportunityItem>(`/opportunity-items/${itemId}`, payload)
}

export function deleteItem(itemId: number) {
  return api.delete<null>(`/opportunity-items/${itemId}`)
}

export function listStages() {
  return api.get<Stage[]>('/opportunity-stages')
}

export function listLossReasons() {
  return api.get<LossReason[]>('/loss-reasons')
}

export function getFunnel() {
  return api.get<FunnelRow[]>('/opportunities/funnel')
}
