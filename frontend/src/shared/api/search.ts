import { api } from './client'

export interface SearchItem {
  id: number
  customer_id?: number
  title: string
  subtitle: string
}

export interface SearchGroup {
  type: string
  label: string
  route: string
  items: SearchItem[]
}

export interface SearchResult {
  keyword: string
  total: number
  groups: SearchGroup[]
}

export function globalSearch(keyword: string, limit = 5) {
  return api.get<SearchResult>('/search', { keyword, limit })
}
