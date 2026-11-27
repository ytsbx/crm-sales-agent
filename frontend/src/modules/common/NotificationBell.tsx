import { formatBeijingTime, formatBeijingClock } from '../../shared/beijing-time'
import { humanizeBackendText } from '../../shared/humanize-text'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Popconfirm, Popover, Tag, Toast } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'

import {
  dismissNotification,
  dismissReadNotifications,
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  redispatchNotification,
  type NotificationRow,
} from '../../shared/api/analytics'

const LINK_BY_TYPE: Partial<Record<string, (id: number) => string>> = {
  customer: (id) => `/customers/${id}?tab=logs`,
  lead: (id) => `/leads?lead=${id}`,
  opportunity: (id) => `/opportunities/${id}`,
  quote: (id) => `/quotes/${id}`,
  order: (id) => `/orders/${id}`,
  sample: (id) => `/samples/${id}`,
  approval: (id) => `/quotes/${id}`,
  payment: (id) => `/orders/${id}`,
  task: () => '/tasks',
  contract: (id) => `/documents?contract=${id}`,
}

export default function NotificationBell() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [open, setOpen] = useState(false)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [dateRange, setDateRange] = useState<[Date, Date] | null>(null)
  const [page, setPage] = useState(1)
  const listRef = useRef<HTMLDivElement>(null)
  const fmtDay = (d: Date) => {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }
  const dateFrom = dateRange ? fmtDay(dateRange[0]) : undefined
  const dateTo = dateRange ? fmtDay(dateRange[1]) : undefined

  const countQuery = useQuery({
    queryKey: ['notifications-unread'],
    queryFn: getUnreadCount,
    refetchInterval: 60_000,
  })
  const listQuery = useQuery({
    queryKey: ['notifications', unreadOnly, dateFrom, dateTo, page],
    queryFn: () => listNotifications(unreadOnly, dateFrom, dateTo, page),
    enabled: open,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['notifications-unread'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const readMutation = useMutation({
    mutationFn: (id: number) => markNotificationRead(id),
    onError: (error: Error) => Toast.error(error.message),
    onSuccess: refresh,
  })
  const readAllMutation = useMutation({
    mutationFn: markAllNotificationsRead,
    onError: (error: Error) => Toast.error(error.message),
    onSuccess: refresh,
  })
  const dismissMutation = useMutation({
    mutationFn: (id: number | null) => id === null ? dismissReadNotifications() : dismissNotification(id),
    onSuccess: (_, id) => {
      refresh()
      Toast.success(id === null ? '已读通知已清除' : '通知已移除')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  // 补投（文档 §六）：失败的通知能人工重发，不必去重跑业务动作——
  // 重跑业务动作会被业务事件去重挡住，这条通知就永远发不出去了
  const redispatchMutation = useMutation({
    mutationFn: (id: number) => redispatchNotification(id),
    onSuccess: (data) => {
      // ⚠️ **别把后端的原因丢掉**（2026-10-13 修）。后端返回的是
      // `仍未能投出：推送已临时关闭（WECOM_PUSH_OFF）` —— 一句话就说清了
      // "为什么还是没出去"；而这里原来只用了 `data.sent`，弹一句
      // 「仍未投出，请看失败原因」，等于让用户自己去翻（主人今天的困惑就是这么来的）。
      // 现在直接把原因显示出来，用户不用再找。
      if (data.sent) {
        Toast.success('已补投')
      } else {
        // 后端原因里可能带内部开关名（如「推送已临时关闭（WECOM_PUSH_OFF）」），
        // 展示前把括号里的英文环境变量抹掉，只留人话（2026-11-17 视觉审视 5.3）
        Toast.warning(`仍未投出：${humanizeBackendText(data.wecom_error) || '原因未记录'}`)
      }
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const items = listQuery.data?.items ?? []
  const count = countQuery.data?.count ?? 0
  const total = listQuery.data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / (listQuery.data?.page_size ?? 10)))
  if (listQuery.data && !listQuery.isFetching && page > lastPage) setPage(lastPage)
  useEffect(() => {
    listRef.current?.scrollTo({ top: 0 })
  }, [page, unreadOnly, dateFrom, dateTo])

  const content = (
    <div className="notice-panel">
      <div className="notice-panel-head">
        <strong>通知</strong>
        <div className="notice-actions">
          <Button size="small" theme="borderless" disabled={!count || dismissMutation.isPending} loading={readAllMutation.isPending} onClick={() => readAllMutation.mutate()}>
            全部已读
          </Button>
          <Popconfirm title="清除所有已读通知？" content="会从你的通知列表移除所有已读通知，包括当前筛选之外的通知。" onConfirm={() => dismissMutation.mutate(null)}>
            <Button size="small" theme="borderless" disabled={dismissMutation.isPending || readAllMutation.isPending}>清除已读</Button>
          </Popconfirm>
        </div>
      </div>
      <div className="notice-filters">
        <div className="notice-views" role="group" aria-label="查看通知">
          {([false, true] as const).map(onlyUnread => (
            <button key={String(onlyUnread)} type="button" aria-pressed={unreadOnly === onlyUnread} onClick={() => {
              setUnreadOnly(onlyUnread)
              setPage(1)
            }}>
              {onlyUnread ? '未读' : '全部'}
            </button>
          ))}
        </div>
        <DatePicker
          type="dateRange"
          size="small"
          density="compact"
          placeholder={['开始', '结束']}
          showClear
          value={dateRange ?? undefined}
          onChange={(value) => {
            setDateRange(Array.isArray(value) && value.length === 2 ? value as [Date, Date] : null)
            setPage(1)
          }}
          style={{ width: 236 }}
        />
      </div>
      <div className="notice-list" ref={listRef} aria-busy={listQuery.isFetching}>
        {listQuery.isPending ? <div className="notice-empty">正在读取通知…</div> :
          listQuery.isError ? <div className="notice-empty"><Button size="small" onClick={() => void listQuery.refetch()}>读取失败，重试</Button></div> :
            !items.length && <div className="notice-empty">{unreadOnly ? '暂无未读通知' : '暂无通知'}</div>}
        {items.map((item: NotificationRow) => {
          const type = item.business_type ?? ''
          const isRecycle = type === 'pool_recycle'
          const build = LINK_BY_TYPE[type]
          const destination = isRecycle
            ? (can('customer:view') ? '/customers?tab=review' : '/pool-recycle')
            : (build && item.business_id ? build(item.business_id) : undefined)
          const actionLabel = !destination ? '标为已读'
            : isRecycle ? '去回收复核'
            : type === 'lead' ? '查看线索'
            : type === 'contract' ? '查看合同'
            : type === 'task' ? '打开销售任务'
            : '查看相关记录'
          const message = isRecycle
            ? item.content?.replace('系统设置 → 业务规则 → 回收待复核', '客户中心 → 回收复核')
            : item.content
          const Body = destination || !item.read ? 'button' : 'div'
          return (
            <article key={item.id} className="notice-item">
              <div className="notice-item-head">
                <Body type={Body === 'button' ? 'button' : undefined} className="notice-item-main" onClick={Body === 'button' ? () => {
                  if (!item.read) readMutation.mutate(item.id)
                  if (destination) {
                    setOpen(false)
                    navigate(destination)
                  }
                } : undefined}>
                  <span className="notice-item-title">
                    {!item.read && <span className="notice-unread-dot" role="img" aria-label="未读" />}
                    <span style={{ fontWeight: item.read ? 500 : 650 }}>{item.title}</span>
                  </span>
                  {message && <span className="notice-item-content">{message}</span>}
                  {Body === 'button' && <span className="notice-item-action">{actionLabel}</span>}
                </Body>
                <Button
                  size="small"
                  theme="borderless"
                  type="tertiary"
                  aria-label={`移除通知：${item.title}`}
                  disabled={dismissMutation.isPending}
                  onClick={() => dismissMutation.mutate(item.id)}
                >移除</Button>
              </div>
              <div
                style={{
                  color: 'var(--crm-text-3)',
                  fontSize: 11,
                  marginTop: 6,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  flexWrap: 'wrap',
                }}
              >
                <span>{formatBeijingTime(item.created_at)}</span>
                {item.wecom_status === 'failed' && (
                  <Tag color="red" type="light" size="small">
                    企微投递失败
                  </Tag>
                )}
                {item.wecom_status === 'skipped' && (
                  <Tag color="grey" type="light" size="small">
                    未投递
                  </Tag>
                )}
                {item.can_redispatch && (
                  <>
                    <span onClick={(event) => event.stopPropagation()}>
                      <Popconfirm title="确认补投这条通知？" content="将尝试重新向接收人发送通知。" onConfirm={() => redispatchMutation.mutate(item.id)} disabled={redispatchMutation.isPending}>
                        <button type="button" className="crm-text-action" style={{ color: 'var(--crm-primary)' }}>{redispatchMutation.isPending ? '补投中…' : '补投'}</button>
                      </Popconfirm>
                    </span>
                    {/* 入口**留着**（主人定的口径：不许"点了没反应也不说为什么"），
                        但把"这次点了会不会有用"讲在明处：
                        卡在配置上的（推送开着没、绑没绑企微）点了也是同一结果。 */}
                    {item.redispatch_hint && (
                      <span style={{ color: 'var(--crm-text-3)', fontSize: 11 }}>
                        （{humanizeBackendText(item.redispatch_hint)}）
                      </span>
                    )}
                  </>
                )}
              </div>
              {/*
                ⚠️ 原因**两种状态都要显示**（2026-10-13 修）：原来只在 failed 时渲染，
                于是「未投递」那一行看不出任何原因 —— 而"未投递"恰恰是最需要解释的
                （用户会以为"没投递"就是还没发，其实很多时候是**永远不会发**）。
                颜色区分：失败是红（真出错了），未投递是灰（环境/配置所致，不是错误）。
              */}
              {(item.wecom_status === 'failed' || item.wecom_status === 'skipped') && (
                <div
                  style={{
                    color: item.wecom_status === 'failed' ? 'var(--crm-error, #ba1a1a)' : 'var(--crm-text-3)',
                    fontSize: 11,
                    marginTop: 2,
                  }}
                >
                  {/* 后端原文可能带内部开关名（WECOM_PUSH_OFF / WECOM_AGENT_ID），
                      列表里一律换成业务说法；找不到对应说法时兜底抹掉括号代号（R-01） */}
                  {humanizeBackendText(item.wecom_error) ||
                    (item.wecom_status === 'failed' ? '投递失败' : '未投递')}
                  {item.wecom_status === 'failed' &&
                    (item.wecom_next_retry_at
                      ? `（${formatBeijingClock(item.wecom_next_retry_at)} 自动重试，已试 ${item.wecom_attempts ?? 0} 次）`
                      : `（已试 ${item.wecom_attempts ?? 0} 次，不再自动重试）`)}
                </div>
              )}
            </article>
          )
        })}
      </div>
      {(page > 1 || lastPage > 1) && (
        <div className="notice-pagination">
          <Button size="small" theme="borderless" disabled={page === 1 || listQuery.isFetching} onClick={() => setPage(page - 1)}>上一页</Button>
          <span>{page} / {lastPage}</span>
          <Button size="small" theme="borderless" disabled={page >= lastPage || listQuery.isFetching} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>
      )}
    </div>
  )

  return (
    <Popover
      content={content}
      trigger="click"
      visible={open}
      onVisibleChange={setOpen}
      position="bottomRight"
    >
      <button
        className="notice-trigger"
        type="button"
        aria-label={count > 0 ? `通知，${count} 条未读` : '通知'}
      >
        <span>通知</span>
        {/* 未读数走行内小圆点：跟着文字排队，不会像绝对定位的角标那样压住「通知」。
            为 0 时整个不渲染，没消息就别摆个 0 在那儿占地方。 */}
        {count > 0 && <span className="notice-count">{count > 99 ? '99+' : count}</span>}
      </button>
    </Popover>
  )
}

