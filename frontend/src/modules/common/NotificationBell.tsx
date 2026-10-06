import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Popover, Tag, Toast } from '@douyinfe/semi-ui'

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

  const countQuery = useQuery({
    queryKey: ['notifications-unread'],
    queryFn: getUnreadCount,
    refetchInterval: 60_000,
  })
  const listQuery = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(false),
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
      Toast[data.sent ? 'success' : 'warning'](
        data.sent ? '已补投' : '仍未投出，请看失败原因',
      )
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
            <span>{new Date(item.created_at).toLocaleString('zh-CN')}</span>
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
              <a
                style={{ color: 'var(--crm-primary)' }}
                onClick={(event) => {
                  event.stopPropagation()
                  redispatchMutation.mutate(item.id)
                }}
              >
                补投
              </a>
            )}
          </div>
          {item.wecom_status === 'failed' && (
            <div style={{ color: 'var(--crm-error, #ba1a1a)', fontSize: 11, marginTop: 2 }}>
              {item.wecom_error || '投递失败'}
              {item.wecom_next_retry_at
                ? `（${new Date(item.wecom_next_retry_at).toLocaleTimeString('zh-CN')} 自动重试，已试 ${item.wecom_attempts ?? 0} 次）`
                : `（已试 ${item.wecom_attempts ?? 0} 次，不再自动重试，可点补投）`}
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
