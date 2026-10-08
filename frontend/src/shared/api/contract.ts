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

/** 衍生件（补充协议 / 续签）：挂在原文档下面的那一层。 */
export interface ContractAmendment {
  id: number
  doc_no: string
  doc_type: string
  doc_type_label: string
  effective_date: string | null
  status: string
  status_label: string
  created_at: string
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
  /** 协议生效日（续签时填） */
  effective_date: string | null
  parent_id: number | null
  /** 关系链往上一级：这份基于哪一份（补充协议 / 续签） */
  parent_doc_no?: string | null
  /** 关系链往下一级：这份被哪几份补充过，只有详情接口返回 */
  amendments?: ContractAmendment[]
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
  query: {
    customer_id?: number
    /** 业务详情页用：只看挂在某一单下面的合同 */
    order_id?: number
    quote_id?: number
    status?: string
    page?: number
    page_size?: number
  } = {},
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
  /** 协议生效日。续签时填：只记到期日处理不了「提前签、未来才生效」的情况 */
  effective_date?: string | null
  /** 补充协议 / 续签：指向被补充的原文档，原件不动、旧版保留 */
  parent_id?: number | null
  /** 续签时是否替代旧协议：勾了才结束旧协议的在办提醒 */
  supersede_parent?: boolean
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
  // **不传文件名**：让 `download` 从响应头 `Content-Disposition` 取服务端给的名字。
  // 服务端对正常存档发 `<单据编号>.pdf`，对**历史副本**发
  // `CT-LEGACY（依据历史数据生成的副本）.pdf`，并另给 `X-Contract-Source`。
  // 固定传 `${doc.doc_no}.pdf` 会把「这是副本」这个辨识**覆盖掉**
  // —— 用户下载完看不出差异，那 11.3 做的那套标识就白做了（复审 11.3）。
  return api.download(`/contract-documents/${doc.id}/download`)
}

export function signContractDocument(docId: number, payload: { file_id: number; note?: string | null }) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/sign`, payload)
}

export function voidContractDocument(docId: number, reason: string) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/void`, { reason })
}
