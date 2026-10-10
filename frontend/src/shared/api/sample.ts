import { api } from './client'
import type { PageResult } from '../types'

/** 样品（PRD §19 / 03-API §26）。 */

export interface SampleItem {
  id: number
  sample_request_id: number
  /** 定制件无 SKU（场景09）：sku_id 为空，改用 inquiry_no / item_name 溯源 */
  sku_id?: number | null
  sku_code?: string | null
  sku_name?: string | null
  specification?: string | null
  unit?: string | null
  inquiry_id?: number | null
  inquiry_no?: string | null
  is_custom?: boolean
  original_quantity?: number | null
  source_snapshot?: SampleSourceLine | null
  quantity: number
  // ---- 车间依据：逐行不同，所以挂在明细上（不是单头）----
  craft?: string | null
  material?: string | null
  drawing_version?: string | null
  remark?: string | null
}

export interface SampleShipment {
  id: number
  sample_request_id: number
  carrier?: string | null
  tracking_no?: string | null
  shipping_fee: number
  shipped_at?: string | null
  signed_at?: string | null
}

/** 制作依据快照里的一项（后端 `serialize_request` 的 `basis_files[]`）。 */
export interface BasisFile {
  file_id: number
  file_name?: string | null
  /** 文件内容的校验值（sha256）：只记文件名证明不了是哪一份文件 */
  checksum?: string | null
  size?: number | null
  category?: string | null
  /** 登记依据时的打样版本：V1、V2 各用各的依据 */
  sample_version?: number | null
  designated_at?: string | null
  designated_by?: number | null
}

export interface SampleRequestRow {
  source_context?: SampleSourceContext | null
  id: number
  opportunity_id?: number | null
  opportunity_title?: string | null
  customer_id?: number | null
  customer_name?: string | null
  contact_id?: number | null
  owner_id?: number | null
  owner_name?: string | null
  status: string
  status_label: string
  /** 审批轮次（§3.2）：驳回后重提 / 已批准后改车间依据会自增。 */
  review_round?: number
  /** 修订版（§3.3）：第几版、取代了谁、又被谁取代（superseded_by 有值即冻结只读）。 */
  version?: number
  parent_id?: number | null
  superseded_by?: number | null
  remark?: string | null
  reject_reason?: string | null
  requested_at?: string | null
  approved_at?: string | null
  shipped_at?: string | null
  signed_at?: string | null
  feedback?: string | null
  // ---- 生产打样资料（文档 §3.5）----
  // 材质 / 工艺 / 图纸版本**不在这里**：它们逐行不同，跟着 items 走。
  purpose?: string | null
  target_completion_date?: string | null
  acceptance_criteria?: string | null
  sample_fee?: number | null
  production_owner_id?: number | null
  made_at?: string | null
  /**
   * 制作依据快照（第一批返修 §3.5）：登记制作完成时**显式指定**照哪几份文件做的。
   * 每项含文件名、校验值（sha256）、当时的打样版本与登记人、时间。
   * 空数组 = 没登记过依据；`null`/缺省 = 这条数据来自加这个字段之前（两者含义不同，
   * 界面上要分开说）。
   */
  basis_files?: BasisFile[] | null
  /** 制作完成事件流水（含说明、时间、登记人） */
  made_events?: { key?: string; at?: string; note?: string | null; by?: number }[] | null
  /**
   * 附件是否已**锁定写入**（2026-10-08 补上附件区时加）。
   *
   * 已登记制作完成、或状态到了寄样/签收 → 锁定：之后上传的附件**只能**归为
   * 「后续补充资料」，否则"制作依据"和"事后补料"就分不开了。
   * 判据在后端（`file/access.sample_write_lock_label_for`），前端**别自己按
   * made_at / status 推** —— 推出来的那份迟早和后端分叉。
   */
  attachment_locked?: boolean
  /** 锁定原因（人话，如「打样单已登记制作完成」）；没锁定时为 null */
  attachment_lock?: string | null
  // 客户确认与签收分开：签收是物流事实，确认是业务事实
  confirm_status: string
  confirm_status_label: string
  customer_confirmed_at?: string | null
  confirm_remark?: string | null
  created_at?: string | null
  items: SampleItem[]
  shipments: SampleShipment[]
}

export function listSamples(query: {
  keyword?: string
  status?: string
  customer_id?: number
  opportunity_id?: number
  owner_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<SampleRequestRow>>('/samples', query)
}

export function getSample(id: number) {
  return api.get<SampleRequestRow>(`/samples/${id}`)
}

export function createSample(payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>('/samples', payload)
}

export function updateSample(id: number, payload: Record<string, unknown>) {
  return api.patch<SampleRequestRow>(`/samples/${id}`, payload)
}

export function approveSample(id: number, approved: boolean, rejectReason?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/approve`, {
    approved,
    reject_reason: rejectReason,
  })
}

/**
 * 已驳回的打样单**原样**重新提交审批。
 *
 * 和「改资料」是两条路：改了东西走编辑接口（会自动回到待审批），一个字都不想改
 * 就走这里，明确表达"原样再报一次"—— 否则跟单只能去改个无关字段骗系统回待审批。
 */
export function resubmitSample(id: number) {
  return api.post<SampleRequestRow>(`/samples/${id}/resubmit`)
}

export function shipSample(id: number, payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>(`/samples/${id}/ship`, payload)
}

export function signSample(id: number, signedAt?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/sign`, { signed_at: signedAt })
}

export function feedbackSample(id: number, feedback: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/feedback`, { feedback })
}

/**
 * 登记制作完成（文档 §3.5；CRM 管不到车间，这里只记事实、不当闸门）。
 *
 * `basisFileIds` 是**制作依据**（第一批返修 §3.5）：这次照哪几份文件做的。
 * 只传 `remark` 而不传它，快照就是空的 —— 事后没人说得清"按哪版图纸做的"，
 * 出了质量问题连比对对象都没有。所以登记页必须让操作人选一次。
 */
export function madeSample(
  id: number,
  remark?: string,
  basisFileIds?: number[],
) {
  return api.post<SampleRequestRow>(`/samples/${id}/made`, {
    remark: remark ?? null,
    basis_file_ids: basisFileIds && basisFileIds.length ? basisFileIds : null,
  })
}

/**
 * 开新修订版（§3.3，口径已确认 A：原单出 V2、旧版冻结只读）。
 *
 * 已制作 / 已寄出之后**不能再原地改**车间依据——那样同一行上留着旧制作时间却写着
 * 新资料，和已经做出来的实物对不上。这里复制出一版新的（版本 +1、指向原单），
 * 原单连同制作/寄送事实冻结保留、随时可回看。
 */
export function reviseSample(id: number, remark?: string) {
  return api.post<SampleRequestRow>(`/samples/${id}/revise`, { remark: remark ?? null })
}

/**
 * 登记客户确认结果。
 *
 * 后端规则：**必须先签收**才能确认——客户收到样品才谈得上接受。
 */
export function confirmSample(
  id: number,
  accepted: boolean,
  remark?: string,
  confirm = false,
) {
  return api.post<SampleRequestRow>(`/samples/${id}/confirm${confirm ? '?confirm=true' : ''}`, {
    accepted,
    remark: remark ?? null,
  })
}

export function listSampleItems(id: number) {
  return api.get<SampleItem[]>(`/samples/${id}/items`)
}

export function addSampleItem(id: number, payload: Record<string, unknown>) {
  return api.post<SampleRequestRow>(`/samples/${id}/items`, payload)
}

/**
 * 改一条明细的车间依据（材质 / 工艺 / 图纸版本）。
 *
 * 后端只收这三个字段（多传会报参数错误，不是静默忽略）。改了车间依据 = 审批批的
 * 那版资料作废，后端会把单据退回「待审批」，所以返回值里的 status 可能变成 pending。
 */
export function updateSampleItem(
  id: number,
  itemId: number,
  payload: { craft?: string | null; material?: string | null; drawing_version?: string | null },
) {
  return api.patch<SampleRequestRow>(`/samples/${id}/items/${itemId}`, payload)
}

export interface SampleSourceRef { quote_version_id?: number; inquiry_id?: number }
export interface SampleSourceContext { type: string; id: number; quote_id?: number; no: string; version: number; is_historical: boolean; approval_status?: string }
export interface SampleSourceLine {
  unit_price?: string | null
  source_item_id: number; name: string; original_quantity: string | null
  specification?: string | null; remark?: string | null
}
export interface SampleSourcePreview {
  source: SampleSourceContext; customer_name: string; opportunity_title?: string | null; items: SampleSourceLine[]
}
export function getSampleSource(source: SampleSourceRef) {
  return api.get<SampleSourcePreview>('/samples/source', source as Record<string, number>)
}
export function createSampleFromSource(payload: SampleSourceRef & {
  request_key: string; items: { source_item_id: number; quantity: number; specification?: string; remark?: string }[]
}) {
  return api.post<SampleRequestRow>('/samples/from-source', payload)
}
