import { api } from './client'
import type { PageResult } from '../types'

export interface ContractTemplate {
  id: number
  doc_type: string
  doc_type_label: string
  name: string
  version: number
  body: string
  enabled: boolean
  is_current: boolean
  created_at: string
}

/** 生成时的抬头快照：下载出来的原件用的是这一组值，不随客户/公司资料变化。 */
export interface ContractHeaderSnapshot {
  company_name?: string | null
  customer_name?: string | null
  order_no?: string | null
  quote_no?: string | null
  quote_version_no?: number | null
}

/** 签署原件（上传回来的扫描件）。与「生成稿」是两个东西，别混。 */
export interface ContractSignedFile {
  file_id: number
  file_name: string
  size: number
  checksum: string | null
  attached_at: string
}

export interface ContractDocument {
  id: number
  doc_no: string
  doc_type: string
  doc_type_label: string
  title: string
  customer_id: number
  customer_name: string | null
  order_id: number | null
  quote_id: number | null
  /** 合同钉死的报价版本。报价后来出 V2/V3 都不影响这一份。 */
  quote_version_id: number | null
  quote_version_no: number | null
  template_id: number
  content_snapshot: string
  filled_data: Record<string, string> | null
  /** 生成时没填上的占位符：字段 -> 没填上的原因（后端已翻译成人话）。正文里保留着 {{...}} 原文。 */
  missing_fields: Record<string, string>
  /** 生成时落盘的那份 PDF 的文件 id；为空说明是这一批之前的老数据，下载会实时渲染 */
  generated_file_id: number | null
  header_snapshot: ContractHeaderSnapshot | null
  /** 签署原件清单，只有详情接口返回 */
  signed_files?: ContractSignedFile[]
  status: string
  status_label: string
  expiry_date: string | null
  parent_id: number | null
  signed_at: string | null
  void_reason: string | null
  created_at: string
}

export function listContractTemplates() {
  return api.get<ContractTemplate[]>('/contract-templates')
}

export function createContractTemplate(payload: {
  doc_type: string
  name: string
  body: string
  enabled?: boolean
}) {
  return api.post<ContractTemplate>('/contract-templates', payload)
}

export function listContractDocuments(
  query: { customer_id?: number; status?: string; page?: number; page_size?: number } = {},
) {
  return api.get<PageResult<ContractDocument>>('/contract-documents', query)
}

export function generateContractDocument(payload: {
  template_id: number
  customer_id: number
  order_id?: number | null
  quote_id?: number | null
  /** 钉死依据的报价版本；只传 order_id 时后端会按订单依据的那一版自动带出 */
  quote_version_id?: number | null
  title?: string | null
  extra_fields: Record<string, string>
  expiry_date?: string | null
  /** 补充协议 / 续签：指向被补充的原文档，原件不动、旧版保留 */
  parent_id?: number | null
  /** 幂等键：同一张弹窗里的重复提交带同一个值，后端据此返回原来那份，而不是再建一份。 */
  request_key?: string
}) {
  return api.post<ContractDocument>('/contract-documents', payload)
}

export function getContractDocument(docId: number) {
  // 详情比列表多返回 `signed_files`（签署原件清单），已签状态下前端要据此给出
  // 「看签回来的那一份」的入口——只给生成稿会让人误以为下载的就是签署件。
  return api.get<ContractDocument>(`/contract-documents/${docId}`)
}

/** 下载**生成稿**（生成时落盘的那一份）。注意不是签署原件。 */
export function downloadContractDocument(doc: { id: number; doc_no: string }) {
  return api.download(`/contract-documents/${doc.id}/download`, `${doc.doc_no}.pdf`)
}

export function signContractDocument(docId: number, payload: { file_id: number; note?: string | null }) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/sign`, payload)
}

export function voidContractDocument(docId: number, reason: string) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/void`, { reason })
}
