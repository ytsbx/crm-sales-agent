import { formatBeijingTime, formatDingTalkTime } from '../../shared/beijing-time'
/** 钉钉返回的已同步快照；不推测尚未返回的未来节点，不把节点通过当整单通过。 */
export interface ApprovalProgressEntry {
  kind: 'task' | 'operation'
  person: string
  node: string
  status: string
  time: string | null
  remark: string
}
const ACTIONS: Record<string, string> = {
  START_PROCESS_INSTANCE: '发起审批', EXECUTE_TASK_NORMAL: '审批处理',
  EXECUTE_TASK_AGENT: '代理审批', APPEND_TASK_BEFORE: '前加签', APPEND_TASK_AFTER: '后加签',
  REDIRECT_TASK: '转交', PROCESS_TASK: '审批处理', FINISH_PROCESS_INSTANCE: '流程结束',
  TERMINATE_PROCESS_INSTANCE: '终止流程', REVOKE_PROCESS_INSTANCE: '撤回',
  ADD_COMMENT: '发表评论', COMMENT: '发表评论', CC: '抄送',
}
const STATES: Record<string, string> = {
  NEW: '待处理', RUNNING: '处理中', COMPLETED: '已处理', CANCELED: '已取消',
  CANCELLED: '已取消', TERMINATED: '已终止', AGREE: '同意', APPROVED: '同意',
  REFUSE: '拒绝', REJECT: '拒绝', NONE: '',
}
const obj = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const str = (value: unknown) => typeof value === 'string' ? value : ''
const rows = (value: unknown) => Array.isArray(value) ? value.map(obj) : []
export const formatApprovalTime = (value: string | null | undefined) => {
  if (!value) return '尚未返回'
  const text = formatBeijingTime(value)
  return text === '-' ? '尚未返回' : text
}
export const formatApprovalEventTime = (value: string | null | undefined) => {
  const text = formatDingTalkTime(value)
  return text === '-' ? '尚未返回' : text
}
export function approvalProgress(result: unknown): ApprovalProgressEntry[] {
  const payload = obj(obj(result).payload)
  const operations = rows(payload.operationRecords)
  const names = new Map(operations.map((row) => [str(row.userId), str(row.showName)] as [string, string]).filter(([id, name]) => id && name))
  const person = (row: Record<string, unknown>) => str(row.showName) || names.get(str(row.userId)) || '钉钉处理人（姓名未返回）'
  const tasks: ApprovalProgressEntry[] = rows(payload.tasks).map((row) => {
    const state = STATES[str(row.status).toUpperCase()] || '状态待核对'
    const decision = STATES[str(row.result).toUpperCase()] || ''
    return { kind: 'task', person: person(row), node: str(row.taskGroupName) || '审批节点',
      status: [state, decision].filter(Boolean).join(' · '), time: str(row.finishTime) || str(row.createTime) || null, remark: '' }
  })
  return [...tasks, ...operations.map((row): ApprovalProgressEntry => {
    const action = ACTIONS[str(row.type).toUpperCase()] || '流程操作'
    const decision = STATES[str(row.result).toUpperCase()] || ''
    return { kind: 'operation', person: person(row), node: action, status: decision || action,
      time: str(row.date) || null, remark: str(row.remark) }
  })]
}
