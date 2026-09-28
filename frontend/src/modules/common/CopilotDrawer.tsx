import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Input, Tag, Toast } from '@douyinfe/semi-ui'

import {
  confirmAgentAction,
  createAgentSession,
  getAgentSession,
  rejectAgentAction,
  sendAgentMessage,
  type AgentActionRow,
} from '../../shared/api/agent'
import { useCopilotStore } from '../../shared/store/copilot'
import type { TagTone } from '../../shared/types'

/**
 * 全局右侧 Copilot 抽屉（UI 设计稿：详情页右侧常驻 AI 栏 / Sales Copilot）。
 *
 * 与 /agent 页面共用同一套会话接口与风控机制，差别只在：
 * 1. 它从**当前路由**自动推出 context_type / context_id，模型因此知道你在看哪条记录
 *    （`agent_sessions.context_type` 这两列一直存在但没被用过，本轮才接上）；
 * 2. 每个页面给一组贴合场景的快捷提问，点一下就发；
 * 3. 写动作仍然是确认卡片，不会自己执行。
 */

const RISK_TONE: Record<string, TagTone> = { L1: 'green', L2: 'orange', L3: 'red' }

interface PageContext {
  type?: string
  id?: number
  title: string
  questions: string[]
}

/** 从路径推出业务上下文与快捷提问。 */
function resolveContext(pathname: string): PageContext {
  const detail = pathname.match(/^\/(customers|opportunities|quotes|orders|products)\/(\d+)/)
  if (detail) {
    const id = Number(detail[2])
    const byType: Record<string, PageContext> = {
      customers: {
        type: 'customer',
        id,
        title: '客户',
        questions: ['这个客户的跟进情况怎么样？', '这个客户在跟的商机和报价有哪些？', '下一步该做什么？'],
      },
      opportunities: {
        type: 'opportunity',
        id,
        title: '商机',
        questions: ['这个商机现在卡在哪一步？', '下一步建议动作是什么？', '帮我把下一步动作记下来'],
      },
      quotes: {
        type: 'quote',
        id,
        title: '报价单',
        questions: ['这张报价的利润情况怎么样？', '价格有没有低于权限、要不要审批？', '帮我生成一封商务函'],
      },
      orders: {
        type: 'order',
        id,
        title: '订单',
        questions: ['这个订单的履约进度怎么样？', '回款还有多少没到账？'],
      },
      products: {
        type: 'product',
        id,
        title: '产品',
        questions: ['这个产品的成本和价格规则是什么？', '最近卖得怎么样？'],
      },
    }
    return byType[detail[1]]
  }

  const byList: Array<[RegExp, PageContext]> = [
    [/^\/customers/, { title: '客户中心', questions: ['我手上有哪些客户很久没跟进了？', '帮我查一下客户「宏远包装」'] }],
    [/^\/leads/, { title: '线索中心', questions: ['有哪些新线索还没分配？'] }],
    [/^\/opportunities/, { title: '商机中心', questions: ['我手上最该先跟的商机是哪个？', '这个月能成几单？'] }],
    [/^\/quotes/, { title: '报价中心', questions: ['有哪些报价还在等审批？'] }],
    [/^\/orders/, { title: '订单中心', questions: ['这个月还有多少货要发？'] }],
    [/^\/pricing/, { title: '核价', questions: ['帮我算一下 ZX-6040-B 报 3000 件、客户宏远包装的建议价'] }],
    [/^\/analytics/, { title: '数据分析', questions: ['本月销售概况怎么样？', '团队里谁业绩最好？'] }],
  ]
  for (const [pattern, ctx] of byList) {
    if (pattern.test(pathname)) return ctx
  }
  return {
    title: '通用问答',
    questions: ['我手上最该先跟的商机是哪个？', '这个月还有多少应收没回来？'],
  }
}

export default function CopilotDrawer() {
  const location = useLocation()
  const queryClient = useQueryClient()
  const open = useCopilotStore((state) => state.open)
  const seed = useCopilotStore((state) => state.seed)
  const close = useCopilotStore((state) => state.close)
  const clearSeed = useCopilotStore((state) => state.clearSeed)

  const [sessionId, setSessionId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const context = useMemo(() => resolveContext(location.pathname), [location.pathname])
  const contextKey = `${context.type ?? ''}:${context.id ?? ''}`

  // 换了业务对象就换一个会话：否则模型会带着上一条报价的上下文回答这一条。
  useEffect(() => {
    setSessionId(null)
  }, [contextKey])

  const detailQuery = useQuery({
    queryKey: ['agent-session', sessionId],
    queryFn: () => getAgentSession(sessionId!),
    enabled: Boolean(sessionId) && open,
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [detailQuery.data?.messages.length])

  const sendMutation = useMutation({
    mutationFn: ({ id, content }: { id: number; content: string }) => sendAgentMessage(id, content),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['agent-session'] })
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const confirmMutation = useMutation({
    mutationFn: (id: number) => confirmAgentAction(id),
    onSuccess: () => {
      Toast.success('动作已执行')
      void queryClient.invalidateQueries({ queryKey: ['agent-session'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const rejectMutation = useMutation({
    mutationFn: (id: number) => rejectAgentAction(id),
    onSuccess: () => {
      Toast.success('已取消该动作')
      void queryClient.invalidateQueries({ queryKey: ['agent-session'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const send = (text: string) => {
    const content = text.trim()
    if (!content || sendMutation.isPending) return
    setInput('')
    if (!sessionId) {
      createAgentSession({
        title: content.slice(0, 20),
        context_type: context.type,
        context_id: context.id,
      }).then((created) => {
        setSessionId(created.id)
        sendMutation.mutate({ id: created.id, content })
      })
      return
    }
    sendMutation.mutate({ id: sessionId, content })
  }

  // 快捷按钮带来的问题：抽屉一打开就自动发出去
  useEffect(() => {
    if (!open || !seed) return
    clearSeed()
    send(seed)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, seed])

  if (!open) return null

  const messages = detailQuery.data?.messages ?? []
  // L2 待确认 + L3 需审批都要出确认按钮——只认 awaiting_confirmation 的话，
  // 挂在全局抽屉里的 L3 动作永远确认不了（Agent 页才有完整状态列表）
  const pending = (detailQuery.data?.actions ?? []).filter(
    (action) => action.status === 'awaiting_confirmation' || action.status === 'approval_required',
  )

  const renderAction = (action: AgentActionRow) => (
    <Card
      key={action.id}
      style={{
        marginBottom: 8,
        borderLeft: `3px solid ${action.risk_level === 'L3' ? 'var(--crm-error)' : 'var(--crm-caution)'}`,
      }}
      bodyStyle={{ padding: 10 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <Tag color={RISK_TONE[action.risk_level] ?? 'grey'} size="small">
          {action.risk_level} · {action.risk_label}
        </Tag>
        <span style={{ fontWeight: 600, fontSize: 12.5 }}>{action.title}</span>
      </div>
      {(action.display?.fields ?? []).map((field) => (
        <div key={field.label} style={{ display: 'flex', fontSize: 12, gap: 6, marginBottom: 2 }}>
          <span style={{ color: 'var(--crm-text-3)', minWidth: 64, flexShrink: 0 }}>{field.label}</span>
          <span style={{ whiteSpace: 'pre-wrap' }}>{field.value}</span>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
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
    </Card>
  )

  return (
    <>
      <div
        onClick={close}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(24, 28, 35, 0.28)',
          zIndex: 900,
        }}
      />
      <aside
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          width: 400,
          maxWidth: '92vw',
          background: 'var(--crm-surface)',
          borderLeft: '1px solid var(--crm-outline)',
          zIndex: 901,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--crm-surface-high)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span className="chip chip-ai">AI</span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>Sales Copilot</div>
            <div style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
              当前上下文：{context.title}
              {context.id ? ` #${context.id}` : ''}
            </div>
          </div>
          <div style={{ flex: 1 }} />
          <Button size="small" theme="borderless" onClick={() => setSessionId(null)}>
            新会话
          </Button>
          <Button size="small" theme="borderless" onClick={close}>
            收起
          </Button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {messages.length === 0 && !sendMutation.isPending && (
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                问点什么，或者直接点下面的常用问题：
              </div>
              {context.questions.map((question) => (
                <Button
                  key={question}
                  theme="light"
                  style={{ justifyContent: 'flex-start', textAlign: 'left', height: 'auto', padding: '8px 12px' }}
                  onClick={() => send(question)}
                >
                  {question}
                </Button>
              ))}
            </div>
          )}

          {messages.map((message) => (
            <div
              key={message.id}
              style={{
                marginBottom: 12,
                display: 'flex',
                justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
              }}
            >
              <div
                style={{
                  maxWidth: '86%',
                  padding: '8px 12px',
                  borderRadius: 'var(--crm-radius)',
                  fontSize: 13,
                  lineHeight: 1.55,
                  whiteSpace: 'pre-wrap',
                  background:
                    message.role === 'user' ? 'var(--crm-primary)' : 'var(--crm-surface-low)',
                  color: message.role === 'user' ? '#fff' : 'var(--crm-text)',
                }}
              >
                {message.content}
              </div>
            </div>
          ))}

          {sendMutation.isPending && (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 12.5, marginBottom: 12 }}>
              Copilot 正在查数据…
            </div>
          )}

          {pending.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 6 }}>
                待你确认的动作
              </div>
              {pending.map(renderAction)}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        <div
          style={{
            padding: 12,
            borderTop: '1px solid var(--crm-surface-high)',
            display: 'flex',
            gap: 8,
          }}
        >
          <Input
            value={input}
            onChange={setInput}
            placeholder="问 Copilot，例如：这张报价的利润怎么样"
            onEnterPress={() => send(input)}
            disabled={sendMutation.isPending}
          />
          <Button theme="solid" loading={sendMutation.isPending} onClick={() => send(input)}>
            发送
          </Button>
        </div>
      </aside>
    </>
  )
}
