/** 统一 HTTP 客户端：自动带 token、统一解包 {code,message,data}、401 自动登出。 */

import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'

import { useAuthStore } from '../store/auth'
import type { Envelope } from '../types'

export class ApiError extends Error {
  code: number

  constructor(message: string, code: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

const http = axios.create({ baseURL: '/api/v1', timeout: 30000 })

/**
 * 从错误响应里挑出「参数校验失败」的具体字段名。
 *
 * 后端这类 400 的信封是 `{code, message:'参数校验失败', data:[{loc:[…], msg, …}]}` ——
 * 真正有用的字段名藏在 `data` 里，只看 `message` 永远是一句笼统的
 * 「参数校验失败」，不看后端代码根本不知道是哪个入参的锅。
 * 这里把 `loc` 中的字段名提出来拼到提示后面（如「参数校验失败：quoted_price」）。
 *
 * `data` 不是数组时直接跳过（业务错误一般是 null），所以不会误伤别的提示文案。
 */
function pickInvalidFields(body: unknown): string[] {
  if (!body || typeof body !== 'object') return []
  const details = (body as { data?: unknown }).data
  if (!Array.isArray(details)) return []
  // body / query / path 这类位置前缀对使用者没意义，去掉只留字段名
  const positions = new Set(['body', 'query', 'path', 'header', 'cookie'])
  const names = details.map((item) => {
    const loc = (item as { loc?: unknown })?.loc
    if (!Array.isArray(loc)) return ''
    const parts = loc.map((part) => String(part))
    if (parts.length > 1 && positions.has(parts[0])) parts.shift()
    return parts.join('.')
  })
  return Array.from(new Set(names.filter((name) => name.length > 0)))
}

http.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const status: number | undefined = error.response?.status
    const body = error.response?.data
    const code: number = body?.code ?? status ?? 0
    const baseMessage: string = body?.message ?? '网络异常，请稍后重试'
    // 校验类错误把出问题的字段名一并带上：只显示「参数校验失败」等于什么都没说
    const invalidFields = pickInvalidFields(body)
    const message =
      invalidFields.length > 0 ? `${baseMessage}：${invalidFields.join('、')}` : baseMessage
    if (status === 401 || code === 40101 || code === 40102) {
      useAuthStore.getState().clear()
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(new ApiError(message, code))
  },
)

async function unwrap<T>(promise: Promise<AxiosResponse<Envelope<T>>>): Promise<T> {
  const response = await promise
  const body = response.data
  if (body.code !== 0) {
    throw new ApiError(body.message, body.code)
  }
  return body.data
}

export const api = {
  get: <T>(url: string, params?: unknown, config?: AxiosRequestConfig) =>
    unwrap<T>(http.get(url, { params, ...config })),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    unwrap<T>(http.post(url, data, config)),
  patch: <T>(url: string, data?: unknown) => unwrap<T>(http.patch(url, data)),
  put: <T>(url: string, data?: unknown) => unwrap<T>(http.put(url, data)),
  delete: <T>(url: string) => unwrap<T>(http.delete(url)),
  /** 下载二进制文件（如报价单 PDF）：走同一个 axios 实例，自动带 token。 */
  download: async (url: string, filename: string) => {
    const response = await http.get(url, { responseType: 'blob' })
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  },
  /**
   * 取二进制内容但不触发下载，交给调用方自己渲染（用于文件预览）。
   * 注意：这类接口返回的是裸二进制，**不是** {code,message,data} 信封，
   * 所以不能走 unwrap；错误体仍是 JSON 信封，由响应拦截器统一抛 ApiError。
   */
  blob: async (url: string): Promise<{ blob: Blob; mime: string }> => {
    const response = await http.get(url, { responseType: 'blob' })
    // axios 的 header 类型是 string | number | boolean | string[] | AxiosHeaders，先归一成字符串
    const mime = String(response.headers['content-type'] ?? 'application/octet-stream')
    return { blob: new Blob([response.data], { type: mime }), mime }
  },
  /**
   * 用 POST 传筛选条件下载文件（导出接口把条件放 body 里）。
   * 触发浏览器下载，不返回内容 —— 与 download 的交付方式一致。
   */
  downloadPost: async (url: string, body: unknown, filename: string) => {
    const response = await http.post(url, body, { responseType: 'blob' })
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  },
  /** 上传 multipart 表单。 */
  upload: <T>(url: string, form: FormData) =>
    unwrap<T>(http.post(url, form, { headers: { 'Content-Type': 'multipart/form-data' } })),
}
