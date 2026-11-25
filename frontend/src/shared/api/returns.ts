import { api } from './client'

export type SalesReturnStatus =
  | 'reviewing'
  | 'pending'
  | 'completed'
  | 'refunded'
  | 'rejected'
  | 'cancelled'
  | 'voided'

export interface SalesReturnItem {
  id: number
  shipment_batch_item_id: number
  order_item_id: number
  batch_id: number
  sku?: string | null
  specification?: string | null
  quantity: number
  quantity_exact: string
  unit_price: number
  unit_price_exact: string
  amount: number
  amount_exact: string
}

export interface SalesReturn {
  id: number
  return_no: string
  order_id: number
  order_no: string
  customer_id: number
  customer_name?: string | null
  status: SalesReturnStatus
  status_label: string
  reason_code: string
  reason_label: string
  reason: string
  return_date: string
  currency: string
  goods_amount: number
  goods_amount_exact: string
  /** 财务人工确认的最终退货货款；未处理为 null。 */
  settled_goods_amount?: number | null
  settled_goods_amount_exact?: string | null
  freight_refund_amount: number
  freight_refund_amount_exact: string
  total_amount: number
  total_amount_exact: string
  offset_amount: number
  offset_amount_exact: string
  refund_amount: number
  refund_amount_exact: string
  refund_reference?: string | null
  settlement_note?: string | null
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  created_at?: string | null
  reviewed_by_name?: string | null
  reviewed_at?: string | null
  review_note?: string | null
  settled_by_name?: string | null
  settled_at?: string | null
  refunded_by_name?: string | null
  refunded_at?: string | null
  cancel_reason?: string | null
  void_reason?: string | null
  items: SalesReturnItem[]
}

export interface ReturnableItem {
  shipment_batch_item_id: number
  order_item_id: number
  sku?: string | null
  specification?: string | null
  shipped_quantity: number
  shipped_quantity_exact: string
  returned_quantity: number
  returned_quantity_exact: string
  remaining_quantity: number
  remaining_quantity_exact: string
  unit_price: number
  unit_price_exact: string
}

export interface ReturnableBatch {
  batch_id: number
  batch_no: number
  actual_ship_date?: string | null
  items: ReturnableItem[]
}

export interface ReturnableOverview {
  order_id: number
  batches: ReturnableBatch[]
  summary: {
    shipped_quantity: number
    shipped_quantity_exact: string
    returned_quantity: number
    returned_quantity_exact: string
    remaining_quantity: number
    remaining_quantity_exact: string
  }
}

export interface ReturnCreatePayload {
  reason_code: 'customer_rejected' | 'quality_issue' | 'wrong_item' | 'other'
  reason: string
  return_date?: string
  remark?: string
  items: Array<{ shipment_batch_item_id: number; quantity: number }>
}

export function getOrderReturns(orderId: number) {
  return api.get<SalesReturn[]>(`/orders/${orderId}/returns`)
}

export function getReturnable(orderId: number) {
  return api.get<ReturnableOverview>(`/orders/${orderId}/returnable`)
}

export function createReturn(orderId: number, payload: ReturnCreatePayload) {
  return api.post<SalesReturn>(`/orders/${orderId}/returns`, payload)
}

export function cancelReturn(returnId: number, reason: string) {
  return api.post<SalesReturn>(`/returns/${returnId}/cancel`, { reason })
}

export function reviewReturn(
  returnId: number,
  decision: 'approve' | 'reject',
  note?: string,
) {
  return api.post<SalesReturn>(`/returns/${returnId}/review`, {
    decision,
    note: note || undefined,
  })
}

export function settleReturn(
  returnId: number,
  settledGoodsAmount: number,
  freightRefundAmount: number,
  settlementNote?: string,
) {
  return api.post<SalesReturn>(`/returns/${returnId}/settle`, {
    settled_goods_amount: settledGoodsAmount,
    freight_refund_amount: freightRefundAmount,
    settlement_note: settlementNote || undefined,
  })
}

export function refundReturn(returnId: number, refundReference: string) {
  return api.post<SalesReturn>(`/returns/${returnId}/refund`, { refund_reference: refundReference })
}

export function voidReturn(returnId: number, reason: string) {
  return api.post<SalesReturn>(`/returns/${returnId}/void`, { reason })
}
