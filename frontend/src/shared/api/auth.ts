import { api } from './client'
import type { CurrentUser, LoginResult } from '../types'

export function login(username: string, password: string) {
  return api.post<LoginResult>('/auth/login', { username, password })
}

export function fetchMe() {
  return api.get<CurrentUser>('/auth/me')
}

/**
 * 登出：**让服务端把这次登录作废**（第十批 10.12）。
 *
 * `token` 显式传进来，而不是让请求拦截器自己去 store 里取 —— 调用方为了
 * 立刻清掉界面，通常是"先清状态、再发登出"。那种顺序下拦截器已经取不到
 * token 了，请求会不带 Authorization 发出去；服务端没有凭据，自然什么也
 * 作废不了，而且这个失败是**静默**的：界面早跳走了，没人会看到那条 401。
 * 显式传参就是为了让"作废"这一步真的发出去。
 */
export function logout(token?: string | null) {
  return api.post<null>('/auth/logout', undefined, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
}

/**
 * 用当前（可能刚过期的）token 换一个新 token（API §2）。
 *
 * 后端允许"过期但在宽限期内"的 token 续期；过期太久会返回 40102，
 * 调用方应当据此跳登录页。
 *
 * ⚠️ 用它的人注意（第十批 10.12）：这个请求可能在**人已经登出之后**才回来。
 * 写回新 token 之前必须先比一次「登录代」（`currentAuthEpoch()`），
 * 对不上就把结果丢掉 —— 否则一个慢半拍的续期响应会把刚做完的退出
 * 悄悄撤销掉，人下次打开页面发现又登着。
 */
export function refreshToken() {
  return api.post<LoginResult>('/auth/refresh')
}

/** 企微网页授权登录（API §2）。企微凭据未配置时后端会明确报错。 */
export function wecomSsoLogin(payload: { wecom_userid: string; state?: string }) {
  return api.post<LoginResult>('/auth/sso/wecom/callback', payload)
}
