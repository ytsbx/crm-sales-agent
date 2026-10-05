import { api, ApiError } from './client'
import { useAuthStore } from '../store/auth'
import type { PageResult } from '../types'

export interface AgentSessionRow {
  id: number
  title: string
  context_type?: string | null
  context_id?: number | null
  updated_at: string
}

export interface AgentExecutionRow {
  id: number
  session_id: number
  action_id?: number | null
  tool_name: string
  risk_level: string
  risk_label?: string
  status: string
  error_message?: string | null
  /** 事后还原"当时他是以什么身份、能看到什么"（API §38 要求） */
  role_snapshot?: string | null
  data_scope_snapshot?: string | null
  input_payload?: Record<string, unknown> | null
  output_payload?: Record<string, unknown> | null
  started_at: string
  finished_at?: string | null
}

export interface AgentMessageRow {
  id: number
  role: string
  content?: string | null
  tool_name?: string | null
  created_at: string
}

export interface AgentActionRow {
  id: number
  tool_name: string
  tool_label?: string
  action_type: string
  risk_level: string
  risk_label: string
  title: string
  payload?: Record<string, unknown> | null
  status: string
  result?: Record<string, unknown> | null
  created_at: string
  confirmed_at?: string | null
  display?: {
    fields: Array<{ label: string; value: string }>
    result_text: string
    can_confirm: boolean
  }
}

export interface AgentToolCall {
  tool: string
  label?: string
  risk: string
  input: Record<string, unknown>
  output: Record<string, unknown>
}

export interface AgentTurnResult {
  reply: string
  actions: AgentActionRow[]
  tool_calls: AgentToolCall[]
}

export function listAgentSessions() {
  return api.get<AgentSessionRow[]>('/agent/sessions')
}

export function createAgentSession(payload: { title?: string; context_type?: string; context_id?: number }) {
  return api.post<{ id: number; title: string }>('/agent/sessions', payload)
}

export function getAgentSession(id: number) {
  return api.get<{
    session: AgentSessionRow
    messages: AgentMessageRow[]
    actions: AgentActionRow[]
  }>(`/agent/sessions/${id}`)
}

export function deleteAgentSession(id: number) {
  return api.delete<null>(`/agent/sessions/${id}`)
}

export function sendAgentMessage(sessionId: number, content: string) {
  return api.post<AgentTurnResult>(`/agent/sessions/${sessionId}/messages`, { content })
}

// ---------------------------------------------------------------- 流式对话（token 级打字机）
//
// SSE 不能走 axios：浏览器端 axios 拿不到逐块到达的原文，要用 fetch + ReadableStream。
// 事件协议与后端 `POST /agent/sessions/{id}/messages/stream` 一一对应：
//   start / user_message / delta（模型文本增量，打字机的来源）/
//   tool_call / notice（如模型未配置）/ error（模型调用失败）/ done（最终结果）。
// 没有 delta 时（未配模型或异常）也能退化工作：等 done 一次性渲染。

export type AgentStreamEventKind =
  | 'start'
  | 'user_message'
  | 'delta'
  | 'tool_call'
  | 'notice'
  | 'error'
  | 'done'

export async function streamAgentMessage(
  sessionId: number,
  content: string,
  onEvent: (kind: AgentStreamEventKind, data: Record<string, unknown>) => void,
): Promise<AgentTurnResult | null> {
  const token = useAuthStore.getState().token
  const resp = await fetch(`/api/v1/agent/sessions/${sessionId}/messages/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
  })
  if (!resp.ok) {
    // 失败时响应体是统一信封，解析出人话再抛；401 与 axios 拦截器同口径处理
    let message = `请求失败（HTTP ${resp.status}）`
    try {
      const body = await resp.json()
      message = body?.message ?? message
      if (resp.status === 401 || body?.code === 40101 || body?.code === 40102) {
        useAuthStore.getState().clear()
        if (window.location.pathname !== '/login') window.location.href = '/login'
      }
    } catch {
      /* 保留默认消息 */
    }
    throw new ApiError(message, 0)
  }
  if (!resp.body) {
    throw new ApiError('当前浏览器不支持流式读取', 0)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: AgentTurnResult | null = null

  // 一帧 = "event: X\ndata: {...}\n\n"；data 是单行 JSON（后端 _sse 保证）
  const dispatch = (frame: string) => {
    let kind = 'message'
    const dataLines: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event: ')) kind = line.slice(7).trim()
      else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    }
    if (dataLines.length === 0) return
    let data: Record<string, unknown>
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      return
    }
    if (kind === 'done') result = data as unknown as AgentTurnResult
    onEvent(kind as AgentStreamEventKind, data)
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (frame.trim()) dispatch(frame)
    }
  }
  return result
}

export function confirmAgentAction(actionId: number) {
  return api.post<{ result: Record<string, unknown>; action: AgentActionRow }>(
    `/agent/actions/${actionId}/confirm`,
  )
}

export function rejectAgentAction(actionId: number, reason?: string) {
  return api.post<AgentActionRow>(`/agent/actions/${actionId}/reject`, { reason })
}

export function listAgentTools() {
  return api.get<Array<{ name: string; label: string; description: string; risk_level: string }>>(
    '/agent/tools',
  )
}

// ---------------------------------------------------------------- 会话消息 / 动作 / 执行（API §37）

export function listAgentMessages(sessionId: number, query: { page?: number; page_size?: number } = {}) {
  return api.get<PageResult<AgentMessageRow>>(`/agent/sessions/${sessionId}/messages`, query)
}

export function getAgentAction(actionId: number) {
  return api.get<AgentActionRow>(`/agent/actions/${actionId}`)
}

/** 取消一个还没执行的动作（与"拒绝"语义不同：这是我自己撤回）。 */
export function cancelAgentAction(actionId: number, reason?: string) {
  return api.post<AgentActionRow>(`/agent/actions/${actionId}/cancel`, { reason })
}

export function listAgentExecutions(query: { page?: number; page_size?: number } = {}) {
  return api.get<PageResult<AgentExecutionRow>>('/agent/executions', query)
}

export function getAgentExecution(executionId: number) {
  return api.get<AgentExecutionRow>(`/agent/executions/${executionId}`)
}

/** 重试失败的**只读**工具调用（写操作重试可能重复写入，后端会拒）。 */
export function retryAgentExecution(executionId: number) {
  return api.post<{ retried_from: number; tool_name: string; output: Record<string, unknown> }>(
    `/agent/executions/${executionId}/retry`,
  )
}

// ---------------------------------------------------------------- 专用分析（API §37 Specialized）
//
// 这些接口的 analysis 部分是**本地确定性计算**，不配模型也能用；
// `commentary` 才是可选的 AI 叙述，没配模型时是 null 并带 commentary_note。

export interface AnalysisEnvelope {
  /** AI 叙述。没配模型 / 生成失败时为 null，此时看 commentary_note。 */
  commentary: string | null
  /** 叙述不可用的原因；有叙述时为 null。前端只在 commentary 为空时展示它。 */
  commentary_note: string | null
  [key: string]: unknown
}

export function agentCustomerSummary(payload: { customer_id: number }) {
  return api.post<AnalysisEnvelope>('/agent/customer-summary', payload)
}

export function agentOpportunityAnalysis(payload: { opportunity_id: number }) {
  return api.post<AnalysisEnvelope>('/agent/opportunity-analysis', payload)
}

export function agentProductRecommendation(payload: {
  customer_id?: number
  opportunity_id?: number
  limit?: number
}) {
  return api.post<AnalysisEnvelope>('/agent/product-recommendation', payload)
}

export function agentPricingAnalysis(payload: {
  sku_id: number
  quantity?: number
  customer_id?: number
}) {
  return api.post<AnalysisEnvelope>('/agent/pricing-analysis', payload)
}

export function agentQuoteDraft(payload: { opportunity_id: number; customer_id?: number }) {
  return api.post<AnalysisEnvelope>('/agent/quote-draft', payload)
}

export function agentFollowupSuggestion(payload: { customer_id?: number; lead_id?: number }) {
  return api.post<AnalysisEnvelope>('/agent/followup-suggestion', payload)
}

export function agentRiskAnalysis(payload: { order_id?: number } = {}) {
  return api.post<AnalysisEnvelope>('/agent/risk-analysis', payload)
}

