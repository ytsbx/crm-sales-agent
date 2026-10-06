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

export interface BizDocArchive {
  /** archived=存档原件；legacy=历史件没有存档，下载的是按快照重建的副本 */
  status: 'archived' | 'legacy' | 'missing'
  file_id?: number | null
  file_sha256?: string | null
  file_size?: number | null
  renderer_version?: string | null
}

export interface BizDocRow {
  id: number
  doc_no: string
  // 后端还有 quote_sheet（对客报价单，出图格式是 xlsx）——类型里原先漏了它，
  // 于是下载时只能写死 .pdf，报价单存下来就是打不开的坏文件
  doc_type: 'sample_request' | 'order_sheet' | 'quote_sheet'
  doc_type_label: string
  title: string
  version: number
  parent_id?: number | null
  status: 'active' | 'draft' | 'void'
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
  /** 生成时存档的原件（§8.10）：没有存档的历史件要能一眼看出来 */
  archive?: BizDocArchive
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
  order_draft_id?: number
  quote_id?: number
  customer_id?: number
}) {
  return api.get<BizDocRow[]>('/biz-docs', query)
}

/**
 * 生成对客 Excel 报价单：金额取自**这一版**报价的快照，不按当前价现算。
 *
 * `requestKey`（§8.9）：同一把键重试返回**原来那一份**；要再出一版就换新键
 * ——所以内容完全一样也照出新版，不会被"内容查重"永久挡住。
 */
export function generateQuoteDoc(quoteVersionId: number, templateId?: number, requestKey?: string) {
  return api.post<BizDocRow>('/biz-docs/quote', {
    quote_version_id: quoteVersionId,
    template_id: templateId ?? null,
    request_key: requestKey,
  })
}

/** 生成打样需求单：来源询价与本次差异一起落快照，原单不变。 */
export function generateSampleDoc(sampleRequestId: number, templateId?: number, requestKey?: string) {
  return api.post<BizDocRow>('/biz-docs/sample-request', {
    sample_request_id: sampleRequestId,
    template_id: templateId ?? null,
    request_key: requestKey,
  })
}

/** 生成下单文件：来源报价与本次差异一起落快照，订单不变。 */
export function generateOrderDoc(orderId: number, templateId?: number, requestKey?: string) {
  return api.post<BizDocRow>('/biz-docs/order', {
    order_id: orderId,
    template_id: templateId ?? null,
    request_key: requestKey,
  })
}

/**
 * 下载文件。
 *
 * `mode='original'`（默认）读**生成时存档的原件**——反复下载字节一致，
 * 之后升级渲染器也不会改变旧件；历史没有存档的文件会拿到"由历史快照重建"的副本
 * （纸面上写着，不会被当成原件）。
 * `mode='state'` 是**状态副本**：按当前状态重出，作废件的"已作废"要在这里看
 * ——存档原件上印的是生成当时的"有效"，它不会（也不该）跟着状态变。
 */
export function downloadBizDoc(
  doc: {
    id: number
    doc_no: string
    doc_type?: BizDocRow['doc_type']
  },
  { mode = 'original', allowRebuild = false }: { mode?: 'original' | 'state'; allowRebuild?: boolean } = {},
) {
  // 扩展名必须跟着**真实出图格式**走：报价单是 Excel，其余是 PDF。
  // 写死 .pdf 的话，客户收到的"报价单"在 Excel 里打不开。
  const ext = doc.doc_type === 'quote_sheet' ? 'xlsx' : 'pdf'
  const query = new URLSearchParams({ mode })
  if (allowRebuild) query.set('allow_rebuild', 'true')
  const suffix = mode === 'state' ? `（状态副本）.${ext}` : `.${ext}`
  return api.download(`/biz-docs/${doc.id}/download?${query.toString()}`, `${doc.doc_no}${suffix}`)
}

export function voidBizDoc(docId: number, reason: string) {
  return api.post<BizDocRow>(`/biz-docs/${docId}/void`, { reason })
}
