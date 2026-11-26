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
  /** 历史字段：旧退货单可能保留批次；2026-11-26 起新退货单为 null。 */
  shipment_batch_item_id?: number | null
  order_item_id: number
  batch_id?: number | null
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
  /** 人工确认的最终退款总额（可含货款和运费）；未确认为 null。 */
  settled_goods_amount?: number | null
  settled_goods_amount_exact?: string | null
  /** 历史兼容字段；新流程固定 0，运费已并入 settled_goods_amount。 */
  freight_refund_amount: number
  freight_refund_amount_exact: string
  total_amount: number
  total_amount_exact: string
  offset_amount: number
  offset_amount_exact: string
  refund_amount: number
  refund_amount_exact: string
  refund_reference?: string | null
  /** 财务登记退款时上传的凭证附件；由文件中心存原件，不复制文件元信息。 */
  refund_attachment_file_id?: number | null
  refund_attachment_file_name?: string | null
  refund_attachment_file_size?: number | null
  refund_attachment_mime_type?: string | null
  settlement_note?: string | null
  remark?: string | null
  created_by?: number | null
  created_by_name?: string | null
  created_at?: string | null
  reviewed_by_name?: string | null
  reviewed_at?: string | null
  review_note?: string | null
  /** 复核人就是登记人本人：界面标「本人复核」，审计里也留痕。 */
  self_reviewed?: boolean
  settled_by_name?: string | null
  settled_at?: string | null
  refunded_by_name?: string | null
  refunded_at?: string | null
  cancel_reason?: string | null
  void_reason?: string | null
  items: SalesReturnItem[]
}

export interface ReturnableItem {
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

export interface ReturnableOverview {
  order_id: number
  /** 按订单明细（产品/规格）汇总，不再返回发货批次。 */
  items: ReturnableItem[]
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
  items: Array<{ order_item_id: number; quantity: number }>
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
  settlementNote?: string,
) {
  return api.post<SalesReturn>(`/returns/${returnId}/settle`, {
    settled_goods_amount: settledGoodsAmount,
    settlement_note: settlementNote || undefined,
  })
}

export function refundReturn(returnId: number, refundReference: string) {
  return api.post<SalesReturn>(`/returns/${returnId}/refund`, { refund_reference: refundReference })
}

/** 财务登记实际退款前上传退款凭证；一个退货单保留一份，换附件先删旧的。 */
export function uploadReturnRefundAttachment(returnId: number, file: File) {
  const form = new FormData()
  form.append('file', file)
  return api.upload<SalesReturn>(`/returns/${returnId}/refund-attachment`, form)
}

export function deleteReturnRefundAttachment(returnId: number) {
  return api.delete<SalesReturn>(`/returns/${returnId}/refund-attachment`)
}

export function downloadReturnRefundAttachment(returnId: number, filename: string) {
  return api.download(`/returns/${returnId}/refund-attachment`, filename)
}

export function voidReturn(returnId: number, reason: string) {
  return api.post<SalesReturn>(`/returns/${returnId}/void`, { reason })
}
