import { api } from './client'

export interface FileRow {
  id: number
  file_name: string
  mime_type?: string | null
  size: number
  storage_provider: string
  /** 后端判定能否内联预览（图片 / PDF / 纯文本）；html、svg 一律为 false */
  previewable?: boolean
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

/**
 * 改文件名（2026-10-08）。
 *
 * 改的是**展示名**：列表里显示的名字、下载时落成本地的名字。
 * 磁盘上的存放位置不动 —— 所以改名字不会搬文件、不会产生"记录指着不存在的路径"。
 *
 * 两种情况会被后端拒（前端别自己猜，原样把原因显示出来就行）：
 * - 合同那类**原件**（客户签回来的扫描件、系统生成的合同稿）不许改名 ——
 *   名字本身是"当时是哪一份"的线索，改掉之后版本说不清；
 * - 没有文件管理权、或这份文件不在你的可见范围内。
 */
export function renameFile(fileId: number, fileName: string) {
  return api.patch<FileRow>(`/files/${fileId}`, { file_name: fileName })
}

export function unlinkBusinessFile(businessFileId: number) {
  return api.delete<null>(`/business-files/${businessFileId}`)
}

export function downloadFile(fileId: number, fileName: string) {
  return api.download(`/files/${fileId}/download`, fileName)
}

/**
 * 取预览内容。走 blob 而不是直接开新窗口：
 * 前端要拿到 mime 决定用 <img> 还是 <iframe> 渲染，
 * 而且接口需要带 Authorization 头，新窗口直接打 URL 是带不上的。
 */
export function fetchFilePreview(fileId: number): Promise<{ blob: Blob; mime: string }> {
  return api.blob(`/files/${fileId}/preview`)
}
