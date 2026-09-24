import { api } from './client'
import type { PageResult } from '../types'

export interface Task {
  id: number
  title: string
  task_type?: string | null
  customer_id?: number | null
  opportunity_id?: number | null
  lead_id?: number | null
  owner_id?: number | null
  owner_name?: string | null
  priority: string
  priority_label: string
  status: string
  status_label: string
  due_at?: string | null
  source: string
  overdue: boolean
  completed_at?: string | null
  completion_note?: string | null
  created_at: string
}

export function listTasks(query: {
  status?: string
  mine?: boolean
  overdue?: boolean
  owner_id?: number
  opportunity_id?: number
  customer_id?: number
  page?: number
  page_size?: number
}) {
  return api.get<PageResult<Task>>('/tasks', query)
}

export function createTask(payload: Record<string, unknown>) {
  return api.post<Task>('/tasks', payload)
}

export function completeTask(id: number, note?: string) {
  return api.post<Task>(`/tasks/${id}/complete`, { completion_note: note })
}

export function cancelTask(id: number) {
  return api.post<Task>(`/tasks/${id}/cancel`)
}

export function postponeTask(id: number, dueAt: string) {
  return api.post<Task>(`/tasks/${id}/postpone`, { due_at: dueAt })
}

export function transferTask(id: number, ownerId: number) {
  return api.post<Task>(`/tasks/${id}/transfer`, { owner_id: ownerId })
}
