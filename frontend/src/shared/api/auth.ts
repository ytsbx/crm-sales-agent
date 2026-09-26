import { api } from './client'
import type { CurrentUser, LoginResult } from '../types'

export function login(username: string, password: string) {
  return api.post<LoginResult>('/auth/login', { username, password })
}

export function fetchMe() {
  return api.get<CurrentUser>('/auth/me')
}

export function logout() {
  return api.post<null>('/auth/logout')
}

/**
 * 用当前（可能刚过期的）token 换一个新 token（API §2）。
 *
 * 后端允许"过期但在宽限期内"的 token 续期；过期太久会返回 40102，
 * 调用方应当据此跳登录页。
 */
export function refreshToken() {
  return api.post<LoginResult>('/auth/refresh')
}

/** 企微网页授权登录（API §2）。企微凭据未配置时后端会明确报错。 */
export function wecomSsoLogin(payload: { wecom_userid: string; state?: string }) {
  return api.post<LoginResult>('/auth/sso/wecom/callback', payload)
}
