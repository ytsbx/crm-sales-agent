import { formatBeijingTime, formatBeijingClock } from '../../shared/beijing-time'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DatePicker, Popover, Switch, Tag, Toast } from '@douyinfe/semi-ui'

import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  redispatchNotification,
  type NotificationRow,
} from '../../shared/api/analytics'

const LINK_BY_TYPE: Record<string, (id: number) => string> = {
  customer: (id) => `/customers/${id}?tab=logs`,
  lead: (id) => `/leads/${id}`,
  opportunity: (id) => `/opportunities/${id}`,
  quote: (id) => `/quotes/${id}`,
  order: (id) => `/orders/${id}`,
  sample: (id) => `/samples/${id}`,
  approval: (id) => `/quotes/${id}`,
  payment: (id) => `/orders/${id}`,
  task: () => '/tasks',
  // 回收预告发给**主管**的那条：直接落到复核那一屏，点进去就能处理。
  // （发给原负责人的那条走 business_type='customer'，点进客户详情 ——
  //   他关心的是"我的哪个客户要没了"，而不是去复核。）
  pool_recycle: () => '/settings?tab=rules',
}

export default function NotificationBell() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  // 过滤条件（2026-10-13 加）：通知只进不出、面板一次只显 50 条，
  // 没有筛选的话"找上周那条"只能一条条翻
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [dateRange, setDateRange] = useState<[Date, Date] | null>(null)
  const fmtDay = (d: Date) => {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }
  const dateFrom = dateRange ? fmtDay(dateRange[0]) : undefined
  const dateTo = dateRange ? fmtDay(dateRange[1]) : undefined
  const filtersActive = unreadOnly || Boolean(dateRange)

  const countQuery = useQuery({
    queryKey: ['notifications-unread'],
    queryFn: getUnreadCount,
    refetchInterval: 60_000,
  })
  const listQuery = useQuery({
    queryKey: ['notifications', unreadOnly, dateFrom, dateTo],
    queryFn: () => listNotifications(unreadOnly, dateFrom, dateTo),
    enabled: open,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['notifications-unread'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const readMutation = useMutation({
    mutationFn: (id: number) => markNotificationRead(id),
    onSuccess: refresh,
  })
  const readAllMutation = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: refresh,
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
        Toast.warning(`仍未投出：${data.wecom_error ?? '原因未记录'}`)
      }
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const items = listQuery.data?.items ?? []
  const count = countQuery.data?.count ?? 0

  const content = (
    <div className="notice-panel">
      <div className="notice-panel-head">
        <span style={{ fontWeight: 600 }}>通知</span>
        <a style={{ color: 'var(--crm-primary)', fontSize: 13 }} onClick={() => readAllMutation.mutate()}>
          全部已读
        </a>
      </div>
      {/* 过滤行：只看未读 + 日期范围。改任何一个，列表立刻按新条件重查 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          padding: '8px 8px 6px',
          borderBottom: '1px solid var(--crm-surface-high)',
          fontSize: 12,
          color: 'var(--crm-text-2)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          只看未读
          <Switch size="small" checked={unreadOnly} onChange={setUnreadOnly} />
        </span>
        <DatePicker
          type="dateRange"
          size="small"
          density="compact"
          placeholder={['开始日期', '结束日期']}
          // Semi 的 DatePicker 不接受 null，要 undefined；项目里通行写法是 `?? undefined`
          // （本文件 45/46 行也是这个写法）。上一版的 `dateRange` 是 `[Date,Date] | null`，
          // tsc 报 TS2322 且 `tsc -b` 整条失败 → 前端构建挂、CI 红。
          value={dateRange ?? undefined}
          onChange={(value) => setDateRange((value as [Date, Date]) ?? null)}
          style={{ width: 220 }}
        />
        {filtersActive && (
          <a
            style={{ color: 'var(--crm-text-3)', marginLeft: 'auto' }}
            onClick={() => {
              setUnreadOnly(false)
              setDateRange(null)
            }}
          >
            清除
          </a>
        )}
      </div>
      {items.length === 0 && <div className="notice-empty">暂无通知</div>}
      {items.map((item: NotificationRow) => (
        <div
          key={item.id}
          className="notice-item"
          style={{ opacity: item.read ? 0.55 : 1 }}
          onClick={() => {
            readMutation.mutate(item.id)
            const build = item.business_type ? LINK_BY_TYPE[item.business_type] : undefined
            if (build && item.business_id) {
              setOpen(false)
              navigate(build(item.business_id))
            }
          }}
        >
          <div style={{ fontSize: 13, fontWeight: item.read ? 400 : 600 }}>{item.title}</div>
          {item.content && (
            <div style={{ color: 'var(--crm-text-2)', fontSize: 12, marginTop: 2 }}>{item.content}</div>
          )}
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
                <a
                  style={{ color: 'var(--crm-primary)' }}
                  onClick={(event) => {
                    event.stopPropagation()
                    redispatchMutation.mutate(item.id)
                  }}
                >
                  补投
                </a>
                {/* 入口**留着**（主人定的口径：不许"点了没反应也不说为什么"），
                    但把"这次点了会不会有用"讲在明处：
                    卡在配置上的（推送开着没、绑没绑企微）点了也是同一结果。 */}
                {item.redispatch_hint && (
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 11 }}>
                    （{item.redispatch_hint}）
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
              {item.wecom_error || (item.wecom_status === 'failed' ? '投递失败' : '未投递')}
              {item.wecom_status === 'failed' &&
                (item.wecom_next_retry_at
                  ? `（${formatBeijingClock(item.wecom_next_retry_at)} 自动重试，已试 ${item.wecom_attempts ?? 0} 次）`
                  : `（已试 ${item.wecom_attempts ?? 0} 次，不再自动重试）`)}
            </div>
          )}
        </div>
      ))}
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
