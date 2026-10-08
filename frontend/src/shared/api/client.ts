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

/**
 * 把后端给的 message 归一成「一定有字」的提示文案。
 *
 * 为什么需要：原来拦截器写的是 `body?.message ?? '网络异常，请稍后重试'`，
 * 但 `??` **只在 undefined / null 时兜底** —— 后端返回 `message: ''`（或
 * 空白串）时兜不住，于是一条 Toast **只剩一个红点、一个字都没有**，
 * 用户完全不知道发生了什么（主人 2026-10-06 反馈的就是这个）。
 *
 * 另一条路径更隐蔽：`unwrap()` 里 `new ApiError(body.message, ...)` 传 null
 * 进去，`new Error(null).message` 会变成**字符串 "null"** 显示在界面上。
 *
 * 所以这里统一判：拿不到可用文字时，退化成一句带错误码的人话
 * （「系统内部错误」也比空白强）。
 */
function readableMessage(raw: unknown, code: number): string {
  const text = typeof raw === 'string' ? raw.trim() : ''
  if (!text || text === 'null' || text === 'undefined') {
    return code
      ? `系统内部错误（错误码 ${code}），请稍后重试或联系管理员`
      : '网络异常，请稍后重试'
  }
  return text
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
    const baseMessage: string = readableMessage(body?.message, code)
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
    // 同样要归一：这里直接透传 body.message，为空时会抛出一条没有文字的提示
    throw new ApiError(readableMessage(body.message, body.code), body.code)
  }
  return body.data
}

/**
 * 从 `Content-Disposition` 响应头里抠出文件名；抠不到返回 null。
 *
 * 优先 `filename*=UTF-8''<百分号编码>`（RFC 5987，中文名都走这条），
 * 其次 `filename="<名字>"`。服务端两种都发时以前者为准 —— 它才是完整的那个。
 */
function filenameFromDisposition(header: unknown): string | null {
  if (typeof header !== 'string' || !header) return null
  const starred = /filename\*\s*=\s*([^;]+)/i.exec(header)
  if (starred) {
    const raw = starred[1].trim().replace(/^["']|["']$/g, '')
    // 形如 UTF-8''%E5%90%88%E5%90%8C.pdf；语言标签用 '' 分隔
    const encoded = raw.includes("''") ? raw.slice(raw.indexOf("''") + 2) : raw
    try {
      return decodeURIComponent(encoded)
    } catch {
      // 编码坏了就退回下面那条 filename=
    }
  }
  const plain = /filename\s*=\s*("([^"]*)"|([^;]+))/i.exec(header)
  if (plain) {
    const value = (plain[2] ?? plain[3] ?? '').trim()
    if (value) return value
  }
  return null
}

export const api = {
  get: <T>(url: string, params?: unknown, config?: AxiosRequestConfig) =>
    unwrap<T>(http.get(url, { params, ...config })),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    unwrap<T>(http.post(url, data, config)),
  patch: <T>(url: string, data?: unknown) => unwrap<T>(http.patch(url, data)),
  put: <T>(url: string, data?: unknown) => unwrap<T>(http.put(url, data)),
  delete: <T>(url: string) => unwrap<T>(http.delete(url)),
  /**
   * 下载二进制文件（如报价单 PDF）：走同一个 axios 实例，自动带 token。
   *
   * `filename` 两种用法：
   * - **传**一个名字：直接用（导出类接口服务端不回 `Content-Disposition`）；
   * - **不传**：从响应头 `Content-Disposition` 取服务端给的名字
   *   （`filename*=UTF-8''...` 优先 —— 中文名走这条 RFC 5987 编码）。
   *
   * 为什么要有"用服务端名字"这一档（第十一批 11.3 复审）：合同的历史副本，
   * 服务端特意把「（依据历史数据生成的副本）」写进了文件名，前端却固定用
   * `${doc_no}.pdf` 覆盖掉 —— 用户下载完根本看不出这是副本。
   */
  download: async (url: string, filename?: string) => {
    const response = await http.get(url, { responseType: 'blob' })
    const name =
      filename ??
      filenameFromDisposition(response.headers['content-disposition']) ??
      'download'
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = name
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
