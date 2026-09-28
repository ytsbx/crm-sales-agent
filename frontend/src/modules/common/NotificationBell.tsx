import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Popover, Tag, Toast } from '@douyinfe/semi-ui'

import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  redispatchNotification,
  type NotificationRow,
} from '../../shared/api/analytics'

const LINK_BY_TYPE: Record<string, (id: number) => string> = {
  approval: (id) => `/quotes/${id}`,
  payment: (id) => `/orders/${id}`,
  task: () => '/tasks',
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
    <div style={{ width: 340, maxHeight: 420, overflow: 'auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
        }}
      >
        <span style={{ fontWeight: 600 }}>通知</span>
        <a style={{ color: 'var(--crm-primary)', fontSize: 13 }} onClick={() => readAllMutation.mutate()}>
          全部已读
        </a>
      </div>
      {items.length === 0 && <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无通知</div>}
      {items.map((item: NotificationRow) => (
        <div
          key={item.id}
          style={{
            padding: '8px 0',
            borderBottom: '1px solid var(--crm-surface-high)',
            cursor: 'pointer',
            opacity: item.read ? 0.55 : 1,
          }}
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
              marginTop: 4,
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
      <Button theme="borderless" style={{ padding: '0 8px' }}>
        <Badge count={count} overflowCount={99}>
          <span style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>通知</span>
        </Badge>
      </Button>
    </Popover>
  )
}
