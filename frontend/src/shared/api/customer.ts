import { api } from './client'
import type { Contact, Customer, PageResult } from '../types'

export interface CustomerQuery {
  keyword?: string
  level?: string
  status?: string
  source?: string
  owner_id?: number
  pool_status?: string
  page?: number
  page_size?: number
}

export interface CustomerPayload {
  name: string
  short_name?: string | null
  region?: string | null
  address?: string | null
  source?: string | null
  level?: string | null
  remark?: string | null
  /** 请求幂等键（第八批 8.15）：同一份表单的多次提交必须用同一把键 */
  request_key?: string
}

// ---------------------------------------------------------------- 子资源响应

/**
 * 转移/分配客户之后，**名下单据跟着走**的结果。
 *
 * 客户换负责人时，原本挂在原负责人名下的商机／打样／报价／订单草稿／订单
 * 以及它们生成的文件会一并改到新负责人名下（后端 `customer/documents.py`）。
 * `skipped` 是"恰好在同一瞬间被别的同事先接走、因此留在同事手里"的那些 ——
 * 后端把张数与类别一并回给页面，用来提示操作者，而不是静默少几张。
 */
export interface DocumentTransfer {
  moved: Record<string, number>
  skipped: Record<string, number>
  moved_total: number
  skipped_total: number
  skipped_labels: string[]
}

/** 转移/分配的返回：客户本体 + 这次单据跟着走的结果。 */
export type CustomerTransferResult = Customer & { document_transfer?: DocumentTransfer }

export interface CustomerOpportunity {
  id: number
  title: string
  stage_id: number | null
  stage_name: string | null
  status: string
  expected_amount: number | null
  expected_close_date: string | null
  owner_id: number | null
  owner_name: string | null
  item_count: number
}

export interface CustomerQuote {
  id: number
  quote_no: string
  customer_id: number
  owner_id: number | null
  status: string
  valid_until: string | null
  current_version_id: number | null
  current_version_no: number | null
  current_version_amount: number | null
  approval_status: string | null
  created_at: string
}

export interface CustomerOrder {
  id: number
  order_no: string
  status: string
  status_label: string
  total_amount: number | null
  received_amount: number | null
  unreceived_amount: number | null
  currency: string
  erp_order_id: string | null
  delivery_date: string | null
  item_count: number
  created_at: string
}

export interface ContactFollowup {
  id: number
  customer_id: number | null
  followup_type: string
  content: string
  customer_feedback: string | null
  next_action: string | null
  owner_id: number | null
  created_at: string
}

export interface ContactWecomFollower {
  wecom_userid: string
  add_time: string | null
  add_way: string | null
  remark: string | null
  status: string
}

export interface ContactWecom {
  bound: boolean
  contact_id: number
  externals: Array<{
    id: number
    external_userid: string
    name: string | null
    type: string | null
    corp_name: string | null
    crm_customer_id: number | null
    normalize_status: string
    last_sync_at: string | null
    followers: ContactWecomFollower[]
  }>
}

export interface ContactDuplicateMatch {
  id: number
  name: string
  customer_id: number | null
  mobile: string | null
  email: string | null
  is_primary: boolean
  score: number
  reasons: string[]
}

export function listCustomers(query: CustomerQuery) {
  return api.get<PageResult<Customer>>('/customers', query)
}

export interface CustomerStageCount {
  stage: string
  label: string
  count: number
}

/** 六阶段分布：当前数据范围内各阶段客户数（阶段由后端自动推导） */
export function listStageDistribution() {
  return api.get<CustomerStageCount[]>('/customers/stage-distribution')
}

export function getCustomer(id: number) {
  return api.get<Customer>(`/customers/${id}`)
}

export function createCustomer(payload: CustomerPayload) {
  return api.post<Customer>('/customers', payload)
}

export function updateCustomer(id: number, payload: Partial<CustomerPayload>) {
  return api.patch<Customer>(`/customers/${id}`, payload)
}

export function listContacts(customerId: number) {
  return api.get<Contact[]>(`/customers/${customerId}/contacts`)
}

export function createContact(customerId: number, payload: Partial<Contact>) {
  return api.post<Contact>(`/customers/${customerId}/contacts`, payload)
}

export function transferCustomer(customerId: number, payload: { owner_id: number | null; reason?: string }) {
  return api.post<CustomerTransferResult>(`/customers/${customerId}/transfer`, payload)
}

/** 主管分配客户负责人（API §7 POST /customers/{id}/assign）。 */
export function assignCustomer(customerId: number, payload: { owner_id: number | null; reason?: string }) {
  return api.post<CustomerTransferResult>(`/customers/${customerId}/assign`, payload)
}

// ---------------------------------------------------------------- 客户子资源（API §7）

export function listCustomerOpportunities(
  customerId: number,
  query: { status?: string; page?: number; page_size?: number } = {},
) {
  return api.get<PageResult<CustomerOpportunity>>(`/customers/${customerId}/opportunities`, query)
}

export function listCustomerQuotes(
  customerId: number,
  query: { status?: string; page?: number; page_size?: number } = {},
) {
  return api.get<PageResult<CustomerQuote>>(`/customers/${customerId}/quotes`, query)
}

export function listCustomerOrders(
  customerId: number,
  query: { status?: string; page?: number; page_size?: number } = {},
) {
  return api.get<PageResult<CustomerOrder>>(`/customers/${customerId}/orders`, query)
}

/** 按筛选条件导出客户 CSV（API §7 POST /customers/export）。 */
export type CustomerExportPurpose =
  | 'customer_follow_up'
  | 'business_analysis'
  | 'management_report'
  | 'data_reconciliation'
  | 'historical_migration'
  | 'other'

export function exportCustomersFiltered(
  payload: {
    purpose: CustomerExportPurpose
    purpose_note?: string
    keyword?: string
    level?: string
    status?: string
    source?: string
    owner_id?: number
    pool_status?: string
  },
  filename = '客户列表.csv',
) {
  return api.downloadPost('/customers/export', payload, filename)
}

// ---------------------------------------------------------------- 联系人子资源（API §8）

export function listContactFollowups(
  contactId: number,
  query: { page?: number; page_size?: number } = {},
) {
  return api.get<PageResult<ContactFollowup>>(`/contacts/${contactId}/followups`, query)
}

/** 该联系人的企微绑定关系；没绑定过返回 bound=false（不是 404）。 */
export function getContactWecom(contactId: number) {
  return api.get<ContactWecom>(`/contacts/${contactId}/wecom`)
}

/** 联系人查重（PRD §5.4 线索转化第 2 步）。 */
export function deduplicateContacts(payload: {
  contact_id?: number
  name?: string
  mobile?: string
  email?: string
}) {
  return api.post<{ matches: ContactDuplicateMatch[]; count: number }>(
    '/contacts/deduplicate',
    payload,
  )
}

/**
 * 把客户放进公海。
 *
 * `reason` 不只是留痕：客户**还在履约中**（在途订单/未结应收/有效正式报价/
 * 在途打样）时，不填原因会被后端拦下；填了原因表示主管**明确要求例外释放**，
 * 这时才放行，并把保护事项与原因一起写进审计（返工单 6.3）。
 */
export function releaseCustomerToPool(customerId: number, reason?: string) {
  return api.post<Customer>(`/customers/${customerId}/release-to-pool`, { reason })
}

export function claimCustomer(customerId: number) {
  return api.post<Customer>(`/customers/${customerId}/claim`)
}

// ---------------------------------------------------------------- 标签与合并
// 对应 03-API §7 里此前缺失的路径

export interface TagRow {
  id: number
  name: string
  type: string
  status: string
  sort_no: number
}

export interface DuplicateMatch {
  id: number
  name: string
  level?: string | null
  region?: string | null
  owner_name?: string | null
  score: number
  reasons: string[]
}

export function listTags(onlyActive?: boolean) {
  return api.get<TagRow[]>('/tags', onlyActive === undefined ? undefined : { only_active: onlyActive })
}

export function createTag(payload: { name: string; type?: string; sort_no?: number }) {
  return api.post<TagRow>('/tags', payload)
}

export function updateTag(id: number, payload: Partial<TagRow>) {
  return api.patch<TagRow>(`/tags/${id}`, payload)
}

export function deleteTag(id: number) {
  return api.delete<null>(`/tags/${id}`)
}

export function attachCustomerTags(customerId: number, tagIds: number[]) {
  return api.post<{ added: number }>(`/customers/${customerId}/tags`, { tag_ids: tagIds })
}

export function detachCustomerTag(customerId: number, tagId: number) {
  return api.delete<null>(`/customers/${customerId}/tags/${tagId}`)
}

export function batchTagCustomers(payload: {
  customer_ids: number[]
  tag_ids: number[]
  mode?: 'add' | 'replace' | 'remove'
}) {
  return api.post<{ affected: number }>('/customers/batch-tag', payload)
}

export function batchTransferCustomers(payload: {
  customer_ids: number[]
  owner_id: number | null
  reason?: string
}) {
  return api.post<{ affected: number }>('/customers/batch-transfer', payload)
}

export function deduplicateCustomers(payload: {
  customer_id?: number
  name?: string
  mobile?: string
  tax_no?: string
  domain?: string
  address?: string
}) {
  return api.post<{ matches: DuplicateMatch[]; count: number }>('/customers/deduplicate', payload)
}

/** 合并影响清单里的一项：某类关联会跟着走多少条。 */
export interface MergeImpactTarget {
  key: string
  label: string
  count: number
}

/** 合并前必须先有人拍板的冲突（专属价格不一致、税号不一致）。 */
export interface MergeConflict {
  key: string
  label: string
  detail: string
  options: { value: string; label: string }[]
  items: Record<string, unknown>[]
  count: number
}

export interface MergePreview {
  source: { id: number; name: string; owner_id?: number | null; tax_no?: string | null }
  target: { id: number; name: string; owner_id?: number | null; tax_no?: string | null }
  targets: MergeImpactTarget[]
  total_links: number
  conflicts: MergeConflict[]
  /** 必须先给口径才能合并的那些冲突 key */
  blocking: string[]
  note: string
}

/** 合并前的**影响清单**（只读）。先看这个再决定合不合。 */
export function getCustomerMergePreview(customerId: number, targetCustomerId: number) {
  return api.get<MergePreview>(`/customers/${customerId}/merge-preview`, {
    target_customer_id: targetCustomerId,
  })
}

export function mergeCustomers(payload: {
  source_customer_id: number
  target_customer_id: number
  reason?: string
  /** 冲突处理口径：`{customer_price: 'keep_target'}`。不带就可能被 422 拒绝。 */
  resolutions?: Record<string, string>
}) {
  return api.post<{
    target_customer_id: number
    source_customer_id: number
    moved: Record<string, number>
    conflicts?: Record<string, string> | null
    merge_log_id: number
  }>('/customers/merge', payload)
}

export function listCustomerMergeLogs(customerId: number) {
  return api.get<
    {
      id: number
      source_customer_id: number
      target_customer_id: number
      operator_id?: number | null
      moved?: Record<string, number> | null
      reason?: string | null
      created_at?: string | null
    }[]
  >(`/customers/${customerId}/merge-logs`)
}

// ---------------------------------------------------------------- 撞单裁定（验收20）
// 文档 §11.4：系统提示证据并走人工裁定；不依建档先后直接覆盖负责人，不误合并。
// 首页给出证据，归属由人写——系统不替业务判"这条生意归谁"。

export interface DuplicateCase {
  id: number
  customer_id: number
  customer_name?: string | null
  candidate_id: number
  candidate_name?: string | null
  score?: number | null
  evidence?: {
    candidate_name?: string | null
    reasons?: string[] | null
    snapshot?: Record<string, unknown> | null
  } | null
  source: string
  status: string
  decision?: string | null
  decision_label?: string | null
  resolved_at?: string | null
  remark?: string | null
  created_at?: string | null
}

/**
 * 撞单待裁定队列（**真分页**，返工单 6.5）。
 *
 * 原来后端 `.limit()` 硬顶 300、且没有总数：第 301 条之后永远打不开，
 * 用户看到的是一个"看着就这么多"的列表，不会想到要翻。
 */
export function listDuplicateCases(query: {
  status?: string
  page?: number
  page_size?: number
} = {}) {
  return api.get<PageResult<DuplicateCase>>('/customer-duplicate-cases', {
    status: 'pending',
    ...query,
  })
}

/** 对一条客户再跑一次查重并开待裁定单（幂等）。 */
export function openDuplicateCases(customerId: number) {
  return api.post<{ opened: number }>(`/customers/${customerId}/duplicate-cases`)
}

export function resolveDuplicateCase(
  caseId: number,
  payload: { decision: string; owner_id?: number | null; remark?: string | null },
) {
  return api.post<DuplicateCase>(`/customer-duplicate-cases/${caseId}/resolve`, payload)
}
