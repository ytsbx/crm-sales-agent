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

/**
 * 一次拿多个业务对象的附件，返回 `{ 业务id: [附件…] }`。
 *
 * 为什么需要它：SKU 列表每行要显示缩略图，逐行调 `listBusinessFiles` 就是 N+1 ——
 * 一个产品有几个型号就发几个请求。批量接口把当前页的 id 一次带上。
 *
 * 后端**逐个判可见性**，看不见的对象不会出现在返回里（而不是整批报错）：
 * 批量接口一报错，"其中一个不可见"就会变成整个列表打不开。
 */
export function listBusinessFilesBatch(businessType: string, businessIds: number[]) {
  const ids = businessIds.join(',')
  return api.get<Record<string, FileRow[]>>(
    `/business/${businessType}/files/batch?business_ids=${encodeURIComponent(ids)}`,
  )
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
