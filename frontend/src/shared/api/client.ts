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
    const message: string = body?.message ?? '网络异常，请稍后重试'
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
  /** 上传 multipart 表单。 */
  upload: <T>(url: string, form: FormData) =>
    unwrap<T>(http.post(url, form, { headers: { 'Content-Type': 'multipart/form-data' } })),
}
