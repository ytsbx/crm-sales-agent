import { api } from './client'

export interface AgentSessionRow {
  id: number
  title: string
  context_type?: string | null
  context_id?: number | null
  updated_at: string
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
