import { api } from './client'

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
  template_id: number
  content_snapshot: string
  filled_data: Record<string, string> | null
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

export function listContractDocuments(query: { customer_id?: number; status?: string } = {}) {
  return api.get<ContractDocument[]>('/contract-documents', query)
}

export function generateContractDocument(payload: {
  template_id: number
  customer_id: number
  order_id?: number | null
  quote_id?: number | null
  title?: string | null
  extra_fields: Record<string, string>
  expiry_date?: string | null
}) {
  return api.post<ContractDocument>('/contract-documents', payload)
}

export function downloadContractDocument(doc: { id: number; doc_no: string }) {
  return api.download(`/contract-documents/${doc.id}/download`, `${doc.doc_no}.pdf`)
}

export function signContractDocument(docId: number, payload: { file_id: number; note?: string | null }) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/sign`, payload)
}

export function voidContractDocument(docId: number, reason: string) {
  return api.post<ContractDocument>(`/contract-documents/${docId}/void`, { reason })
}
