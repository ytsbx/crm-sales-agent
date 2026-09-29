/**
 * 对外单据（打样需求单 / 下单文件）——文档 §3.5、场景12。
 *
 * 口径与后端一致：文件正文取**生成时的快照**，之后改业务资料不影响已出的文件；
 * 重新生成是**新增一版**（V1、V2…），旧版一个字不动，仍然下载得到。
 */

import { api } from './client'

export interface BizDocSource {
  type?: string | null
  id?: number | null
  no?: string | null
  version?: number | null
}

export interface BizDocRow {
  id: number
  doc_no: string
  doc_type: 'sample_request' | 'order_sheet'
  doc_type_label: string
  title: string
  version: number
  parent_id?: number | null
  status: 'active' | 'void'
  status_label: string
  owner_id?: number | null
  customer_id?: number | null
  customer_name?: string | null
  sample_request_id?: number | null
  order_id?: number | null
  inquiry_id?: number | null
  quote_id?: number | null
  source: BizDocSource
  template: { id: number; version: number; name?: string | null }
  content_sha256: string
  item_count: number
  diff_count: number
  void_reason?: string | null
  created_at?: string | null
}

export interface BizDocTemplateRow {
  id: number
  doc_type: string
  doc_type_label: string
  name: string
  version: number
  body: string
  enabled: boolean
  remark?: string | null
  created_at?: string | null
}

export function listBizDocs(query: {
  doc_type?: string
  sample_request_id?: number
  order_id?: number
  customer_id?: number
}) {
  return api.get<BizDocRow[]>('/biz-docs', query)
}

/** 生成打样需求单：来源询价与本次差异一起落快照，原单不变。 */
export function generateSampleDoc(sampleRequestId: number, templateId?: number) {
  return api.post<BizDocRow>('/biz-docs/sample-request', {
    sample_request_id: sampleRequestId,
    template_id: templateId ?? null,
  })
}

/** 生成下单文件：来源报价与本次差异一起落快照，订单不变。 */
export function generateOrderDoc(orderId: number, templateId?: number) {
  return api.post<BizDocRow>('/biz-docs/order', {
    order_id: orderId,
    template_id: templateId ?? null,
  })
}

export function downloadBizDoc(doc: { id: number; doc_no: string }) {
  return api.download(`/biz-docs/${doc.id}/download`, `${doc.doc_no}.pdf`)
}

export function voidBizDoc(docId: number, reason: string) {
  return api.post<BizDocRow>(`/biz-docs/${docId}/void`, { reason })
}
