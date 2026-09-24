import { api } from './client'

export interface FileRow {
  id: number
  file_name: string
  mime_type?: string | null
  size: number
  storage_provider: string
  uploader_name?: string | null
  created_at: string
  business_file_id?: number
  category?: string | null
  remark?: string | null
}

export function listBusinessFiles(businessType: string, businessId: number) {
  return api.get<FileRow[]>(`/business/${businessType}/${businessId}/files`)
}

export async function uploadFile(
  file: File,
  target: { businessType: string; businessId: number; category?: string },
) {
  const form = new FormData()
  form.append('file', file)
  form.append('business_type', target.businessType)
  form.append('business_id', String(target.businessId))
  if (target.category) form.append('category', target.category)
  return api.upload<FileRow>('/files/upload', form)
}

export function deleteFile(fileId: number) {
  return api.delete<null>(`/files/${fileId}`)
}

export function unlinkBusinessFile(businessFileId: number) {
  return api.delete<null>(`/business-files/${businessFileId}`)
}

export function downloadFile(fileId: number, fileName: string) {
  return api.download(`/files/${fileId}/download`, fileName)
}
