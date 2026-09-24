import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Popconfirm, Tag, Toast } from '@douyinfe/semi-ui'

import {
  confirmAgentAction,
  createAgentSession,
  deleteAgentSession,
  getAgentSession,
  listAgentSessions,
  listAgentTools,
  rejectAgentAction,
  sendAgentMessage,
  type AgentActionRow,
  type AgentToolCall,
} from '../../shared/api/agent'
import type { TagTone } from '../../shared/types'

const RISK_TONE: Record<string, TagTone> = { L1: 'green', L2: 'orange', L3: 'red' }

const EXAMPLES = [
  '宏远包装这个客户现在什么情况？',
  '我手上最该先跟的商机是哪个？',
  '帮我算一下 ZX-6040-B 报 3000 件、客户宏远包装的建议价和最低价',
  '这个月还有多少应收没回来？',
]

export default function AgentPage() {
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const [lastTools, setLastTools] = useState<AgentToolCall[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  const contextType = searchParams.get('context')
  const contextId = searchParams.get('id') ? Number(searchParams.get('id')) : undefined

  const sessionsQuery = useQuery({ queryKey: ['agent-sessions'], queryFn: listAgentSessions })
  const toolsQuery = useQuery({ queryKey: ['agent-tools'], queryFn: listAgentTools })
  const detailQuery = useQuery({
    queryKey: ['agent-session', sessionId],
    queryFn: () => getAgentSession(sessionId!),
    enabled: Boolean(sessionId),
  })

  useEffect(() => {
    if (sessionId || sessionsQuery.data === undefined) return
    if (sessionsQuery.data.length > 0 && !contextId) {
      setSessionId(sessionsQuery.data[0].id)
    }
  }, [sessionsQuery.data, sessionId, contextId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [detailQuery.data?.messages.length])

  const createMutation = useMutation({
    mutationFn: (payload: { title?: string; context_type?: string; context_id?: number }) =>
      createAgentSession(payload),
    onSuccess: (data) => {
      setSessionId(data.id)
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const sendMutation = useMutation({
    mutationFn: ({ id, content }: { id: number; content: string }) =>
      sendAgentMessage(id, content),
    onSuccess: (data) => {
      setLastTools(data.tool_calls)
      void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
    },
    onError: (error: Error) => {
      Toast.error(error.message)
    },
  })

  const confirmMutation = useMutation({
    mutationFn: (actionId: number) => confirmAgentAction(actionId),
    onSuccess: () => {
      Toast.success('动作已执行')
      void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['followups'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const rejectMutation = useMutation({
    mutationFn: (actionId: number) => rejectAgentAction(actionId, '用户取消'),
    onSuccess: () => {
      Toast.success('已取消该动作')
      void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteAgentSession(id),
    onSuccess: () => {
      setSessionId(null)
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
    },
  })

  const handleSend = (text: string) => {
    const content = text.trim()
    if (!content) return
    setInput('')
    if (!sessionId) {
      createAgentSession({
        title: content.slice(0, 20),
        context_type: contextType ?? undefined,
        context_id: contextId,
      }).then((created) => {
        setSessionId(created.id)
        sendMutation.mutate({ id: created.id, content })
      })
      return
    }
    sendMutation.mutate({ id: sessionId, content })
  }

  const messages = detailQuery.data?.messages ?? []
  const actions = detailQuery.data?.actions ?? []
  const pendingActions = actions.filter((a) => a.status === 'awaiting_confirmation')
  const archivedActions = actions.filter((a) => a.status !== 'awaiting_confirmation')

  const renderAction = (action: AgentActionRow, pending: boolean) => (
    <Card
      key={action.id}
      style={{
        marginBottom: 8,
        borderLeft: `3px solid ${action.risk_level === 'L3' ? 'var(--crm-error)' : 'var(--crm-caution)'}`,
      }}
      bodyStyle={{ padding: 12 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Tag color={RISK_TONE[action.risk_level] ?? 'grey'}>
          {action.risk_level} · {action.risk_label}
        </Tag>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{action.title}</span>
      </div>
      {action.display?.fields && action.display.fields.length > 0 && (
        <div style={{ marginBottom: 8, display: 'grid', gap: 4 }}>
          {action.display.fields.map((field) => (
            <div key={field.label} style={{ display: 'flex', fontSize: 12.5, gap: 8 }}>
              <span style={{ color: 'var(--crm-text-3)', minWidth: 76, flexShrink: 0 }}>{field.label}</span>
              <span style={{ color: 'var(--crm-text)', whiteSpace: 'pre-wrap' }}>{field.value}</span>
            </div>
          ))}
        </div>
      )}
      {pending ? (
        <div style={{ display: 'flex', gap: 10 }}>
          <Button
            theme="solid"
            size="small"
            loading={confirmMutation.isPending}
            onClick={() => confirmMutation.mutate(action.id)}
          >
            {action.risk_level === 'L3' ? '确认并转审批' : '确认执行'}
          </Button>
          <Button size="small" onClick={() => rejectMutation.mutate(action.id)}>
            取消
          </Button>
        </div>
      ) : (
        <div
          style={{
            fontSize: 12,
            color:
              action.status === 'executed'
                ? 'var(--crm-success)'
                : action.status === 'failed'
                  ? 'var(--crm-error)'
                  : 'var(--crm-text-3)',
          }}
        >
          {action.display?.result_text ?? '已处理'}
        </div>
      )}
    </Card>
  )

  return (
    <div className="page-container" style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16 }}>
      <div className="card-block" style={{ height: 'fit-content' }}>
        <Button
          block
          theme="solid"
          style={{ marginBottom: 12 }}
          onClick={() =>
            createMutation.mutate({
              title: '新会话',
              context_type: contextType ?? undefined,
              context_id: contextId,
            })
          }
        >
          新建会话
        </Button>
        {(sessionsQuery.data ?? []).map((item) => (
          <div
            key={item.id}
            onClick={() => setSessionId(item.id)}
            style={{
              padding: '8px 10px',
              borderRadius: 6,
              cursor: 'pointer',
              marginBottom: 4,
              background: item.id === sessionId ? 'var(--crm-primary-soft)' : 'transparent',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.title}
            </span>
            <Popconfirm title="删除这个会话？" onConfirm={() => deleteMutation.mutate(item.id)}>
              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }} onClick={(e) => e.stopPropagation()}>
                ✕
              </span>
            </Popconfirm>
          </div>
        ))}
        <div style={{ marginTop: 16, color: 'var(--crm-text-3)', fontSize: 12 }}>
          Agent 能做什么（{(toolsQuery.data ?? []).length} 个工具）
        </div>
        <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
          {(toolsQuery.data ?? []).map((tool) => (
            <div key={tool.name} style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
              <Tag color={RISK_TONE[tool.risk_level] ?? 'grey'} size="small">
                {tool.risk_level}
              </Tag>
              <span style={{ color: 'var(--crm-text)' }}>{tool.label}</span>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 8, color: 'var(--crm-text-3)', fontSize: 11, lineHeight: 1.6 }}>
          鼠标停上去可以看到具体能力；函数名（如 search_customers）是系统内部标识，
          界面上不用管它。
        </div>
      </div>

      <div className="card-block" style={{ minHeight: 520, display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflow: 'auto', maxHeight: 560 }}>
          {messages.length === 0 && (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 13, marginBottom: 16 }}>
              问点具体的，比如：
              <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
                {EXAMPLES.map((text) => (
                  <a key={text} style={{ color: 'var(--crm-primary)' }} onClick={() => handleSend(text)}>
                    {text}
                  </a>
                ))}
              </div>
            </div>
          )}
          {messages.map((message) => (
            <div
              key={message.id}
              style={{
                display: 'flex',
                justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 12,
              }}
            >
              <div
                style={{
                  maxWidth: '78%',
                  padding: '10px 12px',
                  borderRadius: 8,
                  fontSize: 13,
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  background: message.role === 'user' ? 'var(--crm-primary)' : 'var(--crm-surface-low)',
                  color: message.role === 'user' ? '#fff' : 'var(--crm-text)',
                }}
              >
                {message.content}
              </div>
            </div>
          ))}

          {lastTools.length > 0 && (
            <div
              style={{
                marginBottom: 12,
                fontSize: 12,
                color: 'var(--crm-text-2)',
                display: 'flex',
                gap: 8,
                flexWrap: 'wrap',
                alignItems: 'center',
              }}
            >
              <span style={{ color: 'var(--crm-text-3)' }}>本次动作：</span>
              {lastTools.map((call, index) => (
                <span key={`${call.tool}-${index}`} title={`${call.label ?? call.tool}（${call.tool}）`}>
                  <Tag color={RISK_TONE[call.risk] ?? 'grey'} size="small">
                    {call.label ?? call.tool}
                  </Tag>
                </span>
              ))}
            </div>
          )}

          {pendingActions.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>
                待你确认的动作
              </div>
              {pendingActions.map((action) => renderAction(action, true))}
            </div>
          )}

          {archivedActions.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 13, color: 'var(--crm-text-3)', marginBottom: 8 }}>历史动作</div>
              {archivedActions.map((action) => renderAction(action, false))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                handleSend(input)
              }
            }}
            placeholder="问点什么…（回车发送）"
            style={{
              flex: 1,
              padding: '10px 12px',
              border: '1px solid var(--crm-surface-high)',
              borderRadius: 6,
              fontSize: 13,
              outline: 'none',
            }}
          />
          <Button
            theme="solid"
            loading={sendMutation.isPending}
            onClick={() => handleSend(input)}
          >
            {sendMutation.isPending ? '思考中…' : '发送'}
          </Button>
        </div>
      </div>
    </div>
  )
}
