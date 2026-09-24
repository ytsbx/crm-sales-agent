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
