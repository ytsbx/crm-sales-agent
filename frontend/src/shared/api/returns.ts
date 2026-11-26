import { api } from './client'

export type SalesReturnStatus =
  | 'reviewing'
  | 'pending'
  | 'warehouse_pending'
  | 'completed'
  | 'refunded'
  | 'settled'
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
  distributor_name?: string | null
  applicant_name?: string | null
  application_date: string
  status: SalesReturnStatus
  status_label: string
  reason_code: string
  reason_label: string
  reason: string
  return_date: string
  currency: string
  goods_amount: number
  goods_amount_exact: string
  /** 业务员填的货款退款。 */
  goods_refund_amount: number
  goods_refund_amount_exact: string
  /** 退款总额（兼容字段）= 货款退款 + 运费退款 + 改价/其他调整。 */
  settled_goods_amount?: number | null
  settled_goods_amount_exact?: string | null
  freight_refund_amount: number
  freight_refund_amount_exact: string
  /** 改价/其他调整，可正可负。 */
  adjustment_amount: number
  adjustment_amount_exact: string
  adjustment_note?: string | null
  total_amount: number
  total_amount_exact: string
  payment_method: string
  receiving_account?: string | null
  /** 申请时上传的《分销商退款登记表》。 */
  application_attachment_file_id?: number | null
  application_attachment_file_name?: string | null
  application_attachment_file_size?: number | null
  application_attachment_mime_type?: string | null
  return_tracking_no?: string | null
  /** 系统自动抵扣客户未付款的金额；不是财务手填。 */
  offset_amount: number
  offset_amount_exact: string
  /** 现金退还给客户的金额 = total_amount - offset_amount。 */
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
  /** 审核人就是申请人本人：界面标「本人审核」，审计里也留痕。 */
  self_reviewed?: boolean
  warehouse_confirmed_by_name?: string | null
  warehouse_confirmed_at?: string | null
  warehouse_note?: string | null
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
  /** 业务员填的退款金额构成，服务端合计总额。 */
  goods_refund_amount: number
  freight_refund_amount: number
  adjustment_amount: number
  adjustment_note?: string
  payment_method: string
  receiving_account: string
  /** 先走 `/files/upload` 上传《分销商退款登记表》，再带 file_id 提交。 */
  application_attachment_file_id: number
  return_tracking_no?: string
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

/**
 * 仅历史旧单：还没有金额的 pending 单由财务补退款总额。
 *
 * 2026-11-26 新流程：业务员申请时填金额构成、主管审核；财务不改金额。
 * 新单金额有误只能驳回重提，正式退款走 `settleReturn`。
 */
export function updateReturnAmount(
  returnId: number,
  refundAmount: number,
  note?: string,
) {
  return api.post<SalesReturn>(`/returns/${returnId}/amount`, {
    refund_amount: refundAmount,
    note: note || undefined,
  })
}

export interface ReturnSettlePayload {
  /** 有现金退还时必填（至少 2 个字）；全额抵扣可不填。 */
  refund_reference?: string
  note?: string
}

/**
 * 财务确认退款：系统自动先冲客户未付款，剩余才现金退。
 * 财务不改金额、不手填抵扣。
 */
export function settleReturn(returnId: number, payload: ReturnSettlePayload) {
  return api.post<SalesReturn>(`/returns/${returnId}/settle`, payload)
}

export interface ReturnWarehousePayload {
  note?: string
  tracking_no?: string
}

/** 仓库/主管确认收到退货；确认后才允许财务退款。 */
export function confirmReturnWarehouse(returnId: number, payload: ReturnWarehousePayload) {
  return api.post<SalesReturn>(`/returns/${returnId}/warehouse-confirm`, payload)
}

/** 历史抵扣单登记现金退款；新退货请优先使用 `settleReturn`。 */
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
