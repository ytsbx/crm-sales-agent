import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Modal, Popconfirm, Tag, Toast, Tooltip } from '@douyinfe/semi-ui'

import {
  confirmAgentAction,
  createAgentSession,
  deleteAgentSession,
  getAgentSession,
  getAgentTurnStatus,
  getAgentOlderMessages,
  listAgentSessions,
  listAgentTools,
  rejectAgentAction,
  streamAgentMessage,
  type AgentActionRow,
  type AgentMessageRow,
  type AgentSessionRow,
  type AgentToolCall,
  type AgentTurnResult,
} from '../../shared/api/agent'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import { getCustomer } from '../../shared/api/customer'
import { newRequestKey } from '../../shared/api/requestKey'
import { useAuthStore } from '../../shared/store/auth'

type TurnAttempt = { requestKey: string; content: string }

function readTurnAttempt(key: string): TurnAttempt | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? 'null')
  } catch {
    return null
  }
}

const RISK_TONE: Record<string, TagTone> = { L1: 'green', L2: 'orange', L3: 'red' }

const EXAMPLES = [
  '宏远包装这个客户现在什么情况？',
  '我手上最该先跟的商机是哪个？',
  '帮我算一下 ZX-6040-B 报 3000 件、客户宏远包装的建议价和最低价',
  '这个月还有多少应收没回来？',
]

export default function AgentPage() {
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const [selectedSession, setSelectedSession] = useState<Pick<AgentSessionRow, 'id' | 'context_type' | 'context_id'> | null>(null)
  const sessionId = selectedSession?.id ?? null
  const [input, setInput] = useState('')
  const [toolsOpen, setToolsOpen] = useState(false)
  const [lastTools, setLastTools] = useState<AgentToolCall[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  // 流式回复：delta 逐段进 streamText（打字机），tool_call 实时进 streamTools。
  // busyRef 与 streaming 并存：setState 是异步的，回车连发要靠 ref 当场拦住。
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamTools, setStreamTools] = useState<AgentToolCall[]>([])
  const busyRef = useRef(false)
  const streamAbortRef = useRef<AbortController | null>(null)

  const userId = useAuthStore((state) => state.user?.id)
  const [attempts, setAttempts] = useState<Record<string, TurnAttempt | null>>({})
  const attemptKey = (id: number) => `crm-agent-turn:${userId}:${id}`
  const currentKey = sessionId ? attemptKey(sessionId) : ''
  const attempt = currentKey && userId != null
    ? (currentKey in attempts ? attempts[currentKey] : readTurnAttempt(currentKey))
    : null
  const saveAttempt = (id: number, value: TurnAttempt | null) => {
    const key = attemptKey(id)
    if (userId != null) {
      try {
        if (value) sessionStorage.setItem(key, JSON.stringify(value))
        else sessionStorage.removeItem(key)
      } catch {
        // 浏览器存储不可用时，仍可在当前页面恢复。
      }
    }
    setAttempts((previous) => ({ ...previous, [key]: value }))
  }
  const recoveryKey = attempt?.requestKey
  const recovery = useQuery({
    queryKey: ['agent-turn', sessionId, recoveryKey],
    queryFn: () => getAgentTurnStatus(sessionId!, recoveryKey!),
    enabled: Boolean(sessionId && attempt && !streaming),
    refetchInterval: (query) => query.state.data?.status === 'running' ? 3000 : false,
  })
  const recoveryStatus = recovery.data?.status
  useEffect(() => {
    if (!sessionId || !recoveryKey || streaming || (recoveryStatus !== 'done' && recoveryStatus !== 'interrupted')) return
    void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
  }, [sessionId, recoveryKey, recoveryStatus, streaming, queryClient])

  useEffect(() => () => streamAbortRef.current?.abort(), [])
  const routeContextType = searchParams.get('context')
  const routeContextId = Number(searchParams.get('id')) || undefined
  const [entry, setEntry] = useState({ key: location.key, type: routeContextType, id: routeContextId })
  const changingEntry = entry.key !== location.key

  /* 从客户详情「跳到 AI」带过来的上下文（/agent?context=customer&id=270）。
     以前 URL 上有参数、页面上却什么标识都没有：用户看到的还是「宏远包装」的示例，
     根本不知道自己问的是哪个客户（2026-11-17 视觉审视 V2-05）。
     这里把来源客户读出来，顶部亮明「当前客户」，示例也换成围绕这家客户的。 */
  const navigate = useNavigate()

  // 会话列表按 id 游标加载更早的：默认最近 30 条，点「加载更早的会话」继续取。
  const sessionsQuery = useInfiniteQuery({
    queryKey: ['agent-sessions'],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      listAgentSessions(pageParam ? { before_id: pageParam, limit: 30 } : { limit: 30 }),
    getNextPageParam: (last) => (last.length === 30 ? last[last.length - 1]?.id : undefined),
  })
  const sessions = sessionsQuery.data?.pages.flatMap((page) => page) ?? []

  const toolsQuery = useQuery({ queryKey: ['agent-tools'], queryFn: listAgentTools })
  const detailQuery = useQuery({
    queryKey: ['agent-session', sessionId],
    queryFn: () => getAgentSession(sessionId!),
    enabled: Boolean(sessionId),
  })
  // 单会话默认只给最新 200 条；更早的按消息 id 游标往「上」补。
  // 加载时保持当前阅读位置，不要把视口拉回顶部/底部。
  const [olderMessages, setOlderMessages] = useState<AgentMessageRow[]>([])
  const [olderHasMore, setOlderHasMore] = useState(false)
  const [olderLoading, setOlderLoading] = useState(false)
  const [olderError, setOlderError] = useState<string | null>(null)
  const chatScrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    setOlderMessages([])
    setOlderHasMore(false)
    setOlderError(null)
  }, [sessionId])
  useEffect(() => {
    if (olderMessages.length === 0 && detailQuery.data?.session.id === sessionId) {
      setOlderHasMore(Boolean(detailQuery.data.messages_has_more))
    }
  }, [detailQuery.data, olderMessages.length, sessionId])
  const loadOlderMessages = async () => {
    if (!sessionId || olderLoading) return
    const oldest = olderMessages[0] ?? detailQuery.data?.messages?.[0]
    if (!oldest) return
    const scroller = chatScrollRef.current
    const distanceFromBottom = scroller ? scroller.scrollHeight - scroller.scrollTop : 0
    setOlderLoading(true)
    setOlderError(null)
    try {
      const page = await getAgentOlderMessages(sessionId, { before_id: oldest.id, limit: 100 })
      setOlderMessages((current) => {
        const known = new Set(current.map((message) => message.id))
        return [...page.items.filter((message) => !known.has(message.id)), ...current]
      })
      setOlderHasMore(page.has_more)
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (scroller) scroller.scrollTop = scroller.scrollHeight - distanceFromBottom
      }))
    } catch (error) {
      setOlderError(error instanceof Error ? error.message : '加载失败')
    } finally {
      setOlderLoading(false)
    }
  }
  const activeSession = detailQuery.data?.session ?? selectedSession
  const contextType = activeSession ? activeSession.context_type : entry.type
  const contextId = activeSession ? activeSession.context_id : entry.id
  const customerContextQuery = useQuery({
    queryKey: ['customer', contextId],
    queryFn: () => getCustomer(contextId!),
    enabled: contextType === 'customer' && Boolean(contextId),
  })
  const contextCustomerName = customerContextQuery.data?.name as string | undefined
  useEffect(() => {
    // 正在回复时继续展示原会话。忙完后再打开新入口，期间禁止向旧会话发送。
    if (!changingEntry || busyRef.current || streaming) return
    setEntry({ key: location.key, type: routeContextType, id: routeContextId })
    setSelectedSession(null)
    setInput('')
    setLastTools([])
  }, [changingEntry, location.key, routeContextType, routeContextId, streaming])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [detailQuery.data?.messages.length, streamText])

  const runStream = async (id: number, content: string, requestKey = newRequestKey()) => {
    saveAttempt(id, { requestKey, content })
    const controller = new AbortController()
    streamAbortRef.current = controller
    const aborted = new Promise<void>((resolve) => controller.signal.addEventListener('abort', () => resolve(), { once: true }))
    const idleReason = '一段时间没有收到回复，正在核对这次提问的状态。'
    let idleTimer: ReturnType<typeof setTimeout> | undefined
    const resetIdleTimer = () => {
      clearTimeout(idleTimer)
      idleTimer = setTimeout(() => controller.abort(idleReason), 120_000)
    }
    resetIdleTimer()
    busyRef.current = true
    setStreaming(true)
    setStreamText('')
    setStreamTools([])
    let textAcc = ''
    let result: AgentTurnResult | null = null
    try {
      result = await streamAgentMessage(id, content, (kind, data) => {
        resetIdleTimer()
        if (kind === 'delta') {
          textAcc += String(data.text ?? '')
          setStreamText(textAcc)
        } else if (kind === 'tool_call') {
          // 工具发生在两段文本之间：补一个段落分隔，气泡里的
          // "我先查一下…" 与最终回复才不会黏成一团
          if (textAcc) textAcc += '\n\n'
          setStreamText(textAcc)
          setStreamTools((prev) => [...prev, data as unknown as AgentToolCall])
        } else if (kind === 'error') {
          Toast.error(String(data.message ?? '对话失败'))
        }
      }, controller.signal, requestKey)
      if (result) {
        saveAttempt(id, null)
        setLastTools(result.tool_calls)
        // 中间轮次的话不在最终回复里：气泡以权威回复收口，
        // 随后 invalidate 拉回服务端消息，两者内容一致
        if (!textAcc.endsWith(result.reply)) setStreamText(result.reply)
      } else {
        Toast.error('连接中断，正在核对这次提问的状态')
      }
    } catch (error) {
      if (controller.signal.reason === idleReason) Toast.warning(idleReason)
      else if (!controller.signal.aborted) Toast.error((error as Error).message)
    } finally {
      clearTimeout(idleTimer)
      // 拉到服务端消息后再撤掉流式气泡，避免"气泡还在、正式消息也来了"的重影
      const refreshHistory = Promise.all([
        queryClient.invalidateQueries({ queryKey: ['agent-session', id] }),
        queryClient.invalidateQueries({ queryKey: ['agent-sessions'] }),
      ])
      // 网络卡住时也需要能继续输入，停止不等历史查询返回。
      if (!controller.signal.aborted) await Promise.race([refreshHistory, aborted])
      streamAbortRef.current = null
      busyRef.current = false
      void queryClient.invalidateQueries({ queryKey: ['agent-turn', id, requestKey] })
      setStreaming(false)
      setStreamText('')
      setStreamTools([])
    }
  }

  const handleSend = (text: string) => {
    const content = text.trim()
    if (!content || busyRef.current || changingEntry || attempt) return
    setInput('')
    if (!sessionId) {
      busyRef.current = true
      setStreaming(true)
      createAgentSession({
        title: content.slice(0, 20),
        context_type: contextType ?? undefined,
        context_id: contextId ?? undefined,
      })
        .then(async (created) => {
          setSelectedSession({ id: created.id, context_type: contextType, context_id: contextId })
          await runStream(created.id, content)
        })
        .catch((error: Error) => {
          busyRef.current = false
          setStreaming(false)
          setInput(content)
          Toast.error(error.message)
        })
      return
    }
    void runStream(sessionId, content)
  }

  const confirmMutation = useMutation({
    mutationFn: (actionId: number) => confirmAgentAction(actionId),
    onSuccess: ({ action }) => {
      Toast.success('动作已执行')
      void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['followups'] })
      if (['create_quote_draft', 'create_quote_version'].includes(action?.tool_name)) {
        const keys = ['quotes', 'quote-versions', 'quote-version', 'quote-version-comparison',
          'customer-quotes', 'opportunity-quote-count', 'opportunity', 'opportunities', 'timeline']
        for (const key of keys) void queryClient.invalidateQueries({ queryKey: [key] })
      }
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
    mutationFn: (id: number) => {
      if (id === sessionId && busyRef.current) throw new Error('等这轮回复结束后再删除会话')
      return deleteAgentSession(id)
    },
    onSuccess: (_data, deletedId) => {
      saveAttempt(deletedId, null)
      if (sessionId === deletedId) {
        setSelectedSession(null)
        setInput('')
        setLastTools([])
      }
      queryClient.removeQueries({ queryKey: ['agent-session', deletedId] })
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const messages = (() => {
    const detailMessages = detailQuery.data?.messages ?? []
    const byId = new Map<number, AgentMessageRow>()
    for (const message of [...olderMessages, ...detailMessages]) byId.set(message.id, message)
    return [...byId.values()].sort((a, b) => a.id - b.id)
  })()
  const actions = detailQuery.data?.actions ?? []
  // L3（转审批）动作落库状态是 approval_required，和 L2 的 awaiting_confirmation
  // 一样都等用户表态——之前只过滤了后者，L3 的确认按钮永远出不来，链路前端断
  const pendingStatuses = new Set(['awaiting_confirmation', 'approval_required'])
  const pendingActions = actions.filter((a) => pendingStatuses.has(a.status))
  const archivedActions = actions.filter((a) => !pendingStatuses.has(a.status))
  // 流式进行中显示实时到达的工具，结束后显示本轮汇总
  const visibleTools = streaming ? streamTools : lastTools

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
          <Button type="primary"
            theme="outline"
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
    <div className="page-container agent-page">
      {/* 左栏原来写死 height: fit-content —— 会话只有一两条时，卡片就一小块飘在上头，
          下面空出大半屏。现在让它跟右栏等高、拉满整页。 */}
      <SectionCard style={{ display: 'flex', flexDirection: 'column' }}>
        {/* 原来是一块通栏的深蓝实心按钮，压在这一页最顶上，比右边的对话框还抢眼。
            换成浅色描边按钮：位置不变、还能点，但不再当色块用。 */}
        {/* 通栏按钮在这栏里像一根长条，跟下面的会话列表分不出主次 ——
            收成靠左的小按钮，位置不变、一眼能点。 */}
        <Button
          size="small"
          theme="outline"
          type="primary"
          style={{ marginBottom: 12, flexShrink: 0, alignSelf: 'flex-start' }}
          disabled={streaming || changingEntry || Boolean(attempt)}
          onClick={() => {
            if (busyRef.current) return
            setSelectedSession(null)
            setInput('')
            setLastTools([])
          }}
        >
          新对话
        </Button>
        {/* 会话列表自己滚，把卡片中间那块空间占住；列表短的时候也不至于空一片。 */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', marginBottom: 4 }}>
        {(sessions ?? []).map((item) => (
          <div
            key={item.id}
            onClick={() => {
              if (busyRef.current || changingEntry) {
                Toast.warning('正在回复中，等这轮结束再切换会话')
                return
              }
              if (item.id !== sessionId) setInput('')
              setSelectedSession(item)
              setLastTools([])
            }}
            className="agent-session-item"
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
            <Popconfirm
              title="删除这个会话？"
              content="聊天和待确认操作会删除，已执行的记录会保留。"
              onConfirm={() => deleteMutation.mutate(item.id)}
            >
              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }} onClick={(e) => e.stopPropagation()}>
                ✕
              </span>
            </Popconfirm>
          </div>
        ))}
        {sessionsQuery.isError && (
          <Button size="small" theme="borderless" onClick={() => void sessionsQuery.refetch()}>
            会话加载失败，点击重试
          </Button>
        )}
        {sessionsQuery.hasNextPage && (
          <Button
            size="small"
            theme="borderless"
            loading={sessionsQuery.isFetchingNextPage}
            onClick={() => {
              if (!sessionsQuery.isFetching) void sessionsQuery.fetchNextPage()
            }}
          >
            加载更早的会话
          </Button>
        )}
        </div>
        {/* 原来是一个 <details> 折叠：主人明确说过「不要这种折叠，巨丑」，
            而且 20 项能力一展开就把左栏撑得老高、把会话列表挤没。
            现在收成一行文字键，点开在弹窗里看全部。 */}
        <Button
          size="small"
          theme="borderless"
          style={{ marginTop: 'auto', flexShrink: 0, alignSelf: 'flex-start' }}
          onClick={() => setToolsOpen(true)}
        >
          能做什么 · {(toolsQuery.data ?? []).length} 项能力 →
        </Button>
        <Modal
          title="AI 助手能做什么"
          visible={toolsOpen}
          onCancel={() => setToolsOpen(false)}
          footer={null}
          width={760}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
              gap: 10,
              maxHeight: '58vh',
              overflowY: 'auto',
              paddingRight: 4,
            }}
          >
            {(toolsQuery.data ?? []).map((tool) => (
              <Tooltip key={tool.name} content={tool.description} position="top">
                <div
                  style={{
                    fontSize: 12.5,
                    display: 'flex',
                    gap: 8,
                    alignItems: 'center',
                    cursor: 'default',
                    padding: '6px 8px',
                    borderRadius: 8,
                    background: 'var(--crm-surface-low)',
                  }}
                >
                  <Tag color={RISK_TONE[tool.risk_level] ?? 'grey'} size="small">
                    {tool.risk_level}
                  </Tag>
                  <span style={{ color: 'var(--crm-text)' }}>{tool.label}</span>
                </div>
              </Tooltip>
            ))}
          </div>
          <p style={{ margin: '12px 0 0', fontSize: 12, color: 'var(--crm-text-3)' }}>
            鼠标停在每一项上，可以看到它具体能做什么。L1 只读取、L2 会写数据、L3 要你确认后才动手。
          </p>
        </Modal>
      </SectionCard>

      <SectionCard style={{ minHeight: 600, display: 'flex', flexDirection: 'column' }}>
        {/* 来源客户标识：有上下文时常驻在对话区顶部，随时看得见「现在问的是谁」 */}
        {contextType === 'customer' && contextId && (
          <div className="agent-context-bar">
            <Tag size="small" color="blue">当前客户</Tag>
            <span className="agent-context-name">
              {/* 客户查不到时不露内部编号（主人明确不喜欢看 ID），只说明「来源客户已不可用」 */}
              {contextCustomerName ??
                (customerContextQuery.isLoading ? '读取中…' : '客户信息已失效')}
            </span>
            <Button
              size="small"
              theme="borderless"
              onClick={() => navigate('/agent')}
            >
              清除上下文
            </Button>
          </div>
        )}
        <div ref={chatScrollRef} className="agent-chat-scroll" style={{ flex: 1, overflow: 'auto' }}>
          {sessionId && detailQuery.isPending && (
            <div className="agent-empty">正在加载会话…</div>
          )}
          {sessionId && (olderHasMore || olderError) && (
            <div style={{ textAlign: 'center', marginBottom: 12 }}>
              <Button
                size="small"
                theme="borderless"
                loading={olderLoading}
                onClick={() => void loadOlderMessages()}
              >
                {olderError ? '加载失败，点击重试' : '加载更早的消息'}
              </Button>
            </div>
          )}
          {sessionId && detailQuery.isError && (
            <div role="alert" className="agent-empty">
              <p>会话加载失败，请重试。</p>
              <Button loading={detailQuery.isFetching} onClick={() => void detailQuery.refetch()}>重新加载会话</Button>
            </div>
          )}
          {messages.length === 0 && (!sessionId || detailQuery.isSuccess) && (
            <div className="agent-empty">
              {/* 这页通体白卡 + 灰块，进来第一眼分不出「这是个助手」还是「一张空表」。
                  加一枚小小的品牌标识，给这页一个能认的身份。 */}
              <div className="agent-empty-mark" aria-hidden>AI</div>
              <h2>{sessionId ? '这段对话还没有消息' : '开始新对话'}</h2>
              <p>直接描述你想查询或处理的业务，也可以试试下面的问题。</p>
              <div className="agent-examples">
                {(() => {
                  /* 有客户上下文时，绝不摆「宏远包装」这类跟来源无关的示例：
                     知道名字就用名字，名字没拿到就用「这个客户」，语义照样指着它。 */
                  if (contextType === 'customer' && contextId) {
                    const who = contextCustomerName ?? '这个客户'
                    return [
                      `${who} 现在跟进到什么阶段？`,
                      `帮我梳理 ${who} 的应收和回款风险`,
                      `给 ${who} 拟一份报价建议`,
                    ]
                  }
                  return EXAMPLES
                })().map((text) => (
                  <button key={text} type="button" disabled={streaming || changingEntry || Boolean(attempt)} onClick={() => handleSend(text)}>
                    {text}
                  </button>
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

          {visibleTools.length > 0 && (
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
              <span style={{ color: 'var(--crm-text-3)' }}>
                {streaming ? '正在：' : '本次动作：'}
              </span>
              {visibleTools.map((call, index) => (
                <span key={`${call.tool}-${index}`} title={`${call.label ?? call.tool}（${call.tool}）`}>
                  <Tag color={RISK_TONE[call.risk] ?? 'grey'} size="small">
                    {call.label ?? call.tool}
                  </Tag>
                </span>
              ))}
            </div>
          )}

          {streaming && (
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 12 }}>
              <div
                style={{
                  maxWidth: '78%',
                  padding: '10px 12px',
                  borderRadius: 8,
                  fontSize: 13,
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  background: 'var(--crm-surface-low)',
                  color: 'var(--crm-text)',
                }}
              >
                <span className={streamText ? 'stream-cursor' : undefined}>
                  {streamText || '思考中…'}
                </span>
              </div>
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

        <div className="agent-composer">
          {attempt && !streaming && sessionId && (
            <div role="status" className="agent-composer-hint" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {recovery.isError ? '暂时查不到这次提问的状态。'
                : recoveryStatus === 'done' ? '已找回这次回复，可继续提问。'
                  : recoveryStatus === 'interrupted' ? '这次提问已中断。已保存的回复和待确认操作可在上方查看。'
                    : recoveryStatus === 'not_received' ? '暂未查到这次提问，可用原编号重试。'
                      : '正在核对这次提问的状态，请稍等。'}
              {recovery.isError && <Button size="small" onClick={() => void recovery.refetch()}>重新查询</Button>}
              {!recovery.isError && recoveryStatus === 'not_received' && (
                <Button size="small" onClick={() => {
                  if (!busyRef.current) void runStream(sessionId, attempt.content, attempt.requestKey)
                }}>重试本次提问</Button>
              )}
              {!recovery.isError && (recoveryStatus === 'done' || recoveryStatus === 'interrupted') && (
                <Button size="small" onClick={() => {
                  if (recoveryStatus === 'interrupted') setInput(attempt.content)
                  saveAttempt(sessionId, null)
                }}>{recoveryStatus === 'done' ? '继续提问' : '重新提问'}</Button>
              )}
            </div>
          )}
          {/* 输入框和发送键包一层：按钮原来按整个 composer 定位，而 composer 底下
              还压着一行「会直接读系统里的…」小字，结果按钮落到小字那一行上，
              和输入框错开了一截（2026-11-21 巡检）。包一层之后按钮就贴着框右下角。 */}
          <div className="agent-input-wrap">
          <textarea
            rows={3}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                handleSend(input)
              }
            }}
            placeholder="输入问题或任务…（Enter 发送，Shift + Enter 换行）"
            style={{
              flex: 1,
              padding: '10px 12px',
              border: '1px solid var(--crm-surface-high)',
              borderRadius: 10,
              fontSize: 14,
              outline: 'none',
              // 聊天框不给拖拽柄：拖出来的高度不会被记住，还会把按钮顶到框外
              resize: 'none',
            }}
          />
          {/* 发送是这一页唯一的主操作，实心蓝；原来用描边蓝（theme="outline"），
              未填内容时几乎看不见按钮在哪（2026-11-21 巡检） */}
          {streaming && streamAbortRef.current ? (
            <Button
              className="agent-send"
              theme="outline"
              onClick={() => {
                streamAbortRef.current?.abort()
                Toast.info('已停止生成，已收到的内容可能稍后出现在历史里')
              }}
            >停止生成</Button>
          ) : <Button type="primary"
            className="agent-send"
            loading={streaming}
            disabled={!input.trim() || changingEntry || Boolean(attempt)}
            onClick={() => handleSend(input)}
          >
            {streaming ? '回复中…' : '发送'}
          </Button>}
          </div>
          {/* 说清楚它到底在干什么：这页最容易被误解成「能随便聊的客服机器人」。 */}
          <p className="agent-composer-hint">
            会直接读系统里的客户、商机、报价、应收数据；涉及改动的动作会先停下来等你确认。
          </p>
        </div>
      </SectionCard>
    </div>
  )
}
