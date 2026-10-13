/** 统一 HTTP 客户端：自动带 token、统一解包 {code,message,data}、401 自动登出。 */

import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'

import { currentAuthEpoch, useAuthStore } from '../store/auth'
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

/**
 * 后端"认不出具体原因"时回的那句固定文案（见 `backend/app/core/errors.py`）。
 *
 * 后端现在会把认得出的校验错误翻成一句人话（如「「数量」这个数太大，超出系统能记的
 * 范围」）；只有认不出时才退回这个固定串。两种情况要分开处理，见下面拦截器。
 */
const GENERIC_VALIDATION_MESSAGE = '参数校验失败'

/**
 * 这次 401 是否属于**当前**登录态？（第十批审查 ⑩）
 *
 * `sentAt` 是请求发出时代次（拦截器写在 config 上），`now` 是此刻的代次。
 * 不相等 = 这个请求是**上一次登录**发出去的，它的 401 不该清掉现在的登录态
 * —— 否则"账号 A 的慢请求迟到 → 把刚登录的账号 B 踢下线"。
 *
 * 抽成独立函数是为了能单测（`ops/auth_epoch_401.test.mjs`）：
 * 拦截器里那段要跑真 axios，测不动；判据本身是纯逻辑。
 *
 * `sentAt === undefined`（老代码没记、或非 axios 来源）按"属于当前"处理 ——
 * 与修之前的行为一致，宁可多清一次也不要漏清（漏清会让用户卡在一个
 * 永远 401 的界面上，那更糟）。
 */
export function belongsToCurrentLogin(sentAt: number | undefined, now: number): boolean {
  return sentAt === undefined || sentAt === now
}

http.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // 记下"这次请求是拿谁的登录态发出去的"（第十批审查 ⑩，2026-10-13）。
  //
  // 为什么必须记：401 处理原来是无条件清登录态的。于是
  //   账号 A 发请求 → A 退出 → B 登录 → A 那个请求这时才回 401
  // 会把**刚登录的 B 踢下线**，还连带清掉 B 的查询缓存
  // （弱网/慢接口下很容易撞上，用户感受是"刚登录就被弹出去"）。
  //
  // 判据用**代次**而不是 token 字符串：`setAuth`/`clear` 都会给代次加一
  // （见 `store/auth.ts` 的说明），所以"代次变了"精确等于"登录态换过了"，
  // 比比较 token 更不容易被"同一 token 重新登录"之类的边界绕过去。
  // 挂在 config 上随请求走，宿主不认这个字段、不影响发包。
  ;(config as { authEpochAtSend?: number }).authEpochAtSend = currentAuthEpoch()
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const status: number | undefined = error.response?.status
    const body = error.response?.data
    const code: number = body?.code ?? status ?? 0
    const baseMessage: string = readableMessage(body?.message, code)
    // 后端只回一句笼统的「参数校验失败」时，把出问题的字段名一并带上 ——
    // 只显示那句等于什么都没说。
    // 但后端**认得出**的错误（数值超出范围、小数位过多、内容太长…）现在会直接
    // 给一句含中文字段名的人话，这时就别再挂一个英文 key 在后面了
    // （2026-10-08 要求：讲人话）。
    const invalidFields = pickInvalidFields(body)
    const message =
      invalidFields.length > 0 && baseMessage === GENERIC_VALIDATION_MESSAGE
        ? `${baseMessage}：${invalidFields.join('、')}`
        : baseMessage
    if (status === 401 || code === 40101 || code === 40102) {
      // ⚠️ 只有"这次失败属于**当前**登录态"时才清（第十批审查 ⑩）。
      // 迟到的 401（旧账号发出的请求）不能把新登录的账号踢下线。
      const sentAt = (error.config as { authEpochAtSend?: number } | undefined)?.authEpochAtSend
      if (belongsToCurrentLogin(sentAt, currentAuthEpoch())) {
        useAuthStore.getState().clear()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
      }
      // 不属于当前登录态：只把这次的错误抛给调用方，不动登录态、不跳转。
      // （调用方本来就会把这条请求的错误显示/忽略掉，与"当前用户被踢出"是两件事。）
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
