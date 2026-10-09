import { api } from './client'
import type { PageResult } from '../types'

export interface Task {
  id: number
  title: string
  task_type?: string | null
  customer_id?: number | null
  contact_id?: number | null
  opportunity_id?: number | null
  lead_id?: number | null
  /** 关联的报价单 / 订单。后端连**编号**一起下发，列表上直接显示「报价 BJ2026xxx」 */
  quote_id?: number | null
  quote_no?: string | null
  order_id?: number | null
  order_no?: string | null
  owner_id?: number | null
  owner_name?: string | null
  priority: string
  priority_label: string
  status: string
  status_label: string
  due_at?: string | null
  source: string
  /**
   * 来源业务对象。任务表没有合同/打样的列，这两类走这里：
   * 合同 = 月结协议到期待办；打样 = 从打样跟进补建任务时带的原打样单。
   */
  source_business_type?: string | null
  source_business_id?: number | null
  /** 来源单据号（如月结协议编号），列表上直接显示给人看 */
  source_doc_no?: string | null
  overdue: boolean
  completed_at?: string | null
  completion_note?: string | null
  created_at: string
  /** 这一行的**版本**（后端任何写入都会抬高）。提交动作时回传，做严格并发校验。 */
  updated_at: string
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

/**
 * 完成 / 取消 / 改期 / 改派 / 转交这五个动作都接受一个可选的 `expectedVersion`
 * —— 就是**用户点按钮那一刻屏幕上那一版**（`task.updated_at`）。
 *
 * 为什么（审查 B2-03）：两个请求真正同时发起时，服务器只知道"你要完成"，
 * 不知道你看到的单子长什么样，于是可能两个都成功（"已完成 + 负责人已换人"）。
 * 带上它之后，服务器能判断"你看到的版本还是当前版本吗"，对不上就返回 409，
 * 由界面提示"请刷新后重试"。
 *
 * **不传仍然可用**（后端兼容口径：没带就不做这层校验），所以老代码不会坏。
 */
export function completeTask(id: number, note?: string, expectedVersion?: string) {
  return api.post<Task>(`/tasks/${id}/complete`, {
    completion_note: note,
    expected_updated_at: expectedVersion,
  })
}

export function cancelTask(id: number, expectedVersion?: string) {
  return api.post<Task>(`/tasks/${id}/cancel`, {
    expected_updated_at: expectedVersion,
  })
}

export function postponeTask(id: number, dueAt: string, expectedVersion?: string) {
  return api.post<Task>(`/tasks/${id}/postpone`, {
    due_at: dueAt,
    expected_updated_at: expectedVersion,
  })
}

export function transferTask(id: number, ownerId: number, expectedVersion?: string) {
  return api.post<Task>(`/tasks/${id}/transfer`, {
    owner_id: ownerId,
    expected_updated_at: expectedVersion,
  })
}

/** 批量完成的返回：成功与跳过的**清单**（含原因），与线索批量分配同一口径。 */
export interface BatchCompleteResult {
  completed: number[]
  skipped: { task_id: number; reason: string }[]
}

/**
 * 批量完成任务（03-API §25）。后端一直有，页面此前没有入口（审查 B2-06）。
 * 单条失败不影响其余：返回成功/跳过清单与原因。
 */
export function batchCompleteTasks(taskIds: number[], note?: string) {
  return api.post<BatchCompleteResult>('/tasks/batch-complete', {
    task_ids: taskIds,
    completion_note: note,
  })
}
