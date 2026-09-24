import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Popover } from '@douyinfe/semi-ui'

import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
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
          <div style={{ color: 'var(--crm-text-3)', fontSize: 11, marginTop: 4 }}>
            {new Date(item.created_at).toLocaleString('zh-CN')}
          </div>
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
