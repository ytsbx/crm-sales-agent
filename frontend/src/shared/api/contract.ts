import { api } from './client'
import type { PageResult } from '../types'
import type { OaApprovalInstance } from './dingtalk'

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
  /**
   * 最新一轮 OA 行的**活数据**（只有详情接口返回）。
   *
   * 与 `dingtalk_oa` 的区别：那个是写进 `filled_data` 的快照，**没有行 id、
   * 也没有 `allowed_actions`** —— 而"结果待人工核对"时的认领/重发/作废按钮
   * 必须按服务端给的动作清单渲染，所以详情单独带出这一行。
   */
  dingtalk_oa_row?: OaApprovalInstance | null
  signed_at: string | null
  void_reason: string | null
  source_type?: 'crm_template' | 'customer_drafted'
  dingtalk_oa?: {
    oa_instance_id?: number
    instance_id?: string | null
    status?: string
    synced_at?: string | null
    result?: Record<string, unknown> | null
    /** 服务端给的"这个状态下还能做什么"，与询价侧同一份口径（类型见 dingtalk.ts 的 `OaAction`）。 */
    allowed_actions?: string[] | null
    /** 上一次发起失败/结果未知的原因；前端要显示它，不能只报成功。 */
    error?: string | null
  } | null
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
  source_type?: 'crm_template' | 'customer_drafted'
}) {
  return api.post<ContractDocument>('/contract-documents', payload)
}

export function startContractDingTalkApproval(docId: number) {
  return api.post<Record<string, unknown>>(`/contract-documents/${docId}/dingtalk-oa`, {})
}

export function withdrawContractDingTalkApproval(docId: number) {
  return api.post<Record<string, unknown>>(`/contract-documents/${docId}/dingtalk-oa-withdraw`, {})
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
