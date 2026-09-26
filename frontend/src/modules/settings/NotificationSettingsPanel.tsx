import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Switch, Toast } from '@douyinfe/semi-ui'

import SectionCard from '../../shared/components/SectionCard'
import {
  getNotificationSettings,
  updateNotificationSettings,
  type NotificationSettings,
} from '../../shared/api/notificationSettings'

/**
 * 通知渠道设置（PRD §25「站内通知 + 企业微信通知」/ API §33）。
 *
 * 设计取舍：企微渠道开着但应用密钥没配时**允许保存**，只给黄色提示。
 * 配置顺序上"先改设置再配密钥"很正常，硬拒会逼人按特定顺序操作；
 * 而投递结果会在每条通知上标 skipped 并写明原因，不会静默丢失。
 */

const EVENT_ROWS: Array<{ key: keyof NotificationSettings['wecom_events']; label: string }> = [
  { key: 'approval', label: '审批提醒（提交 / 通过 / 拒绝）' },
  { key: 'task', label: '任务提醒（指派 / 到期）' },
  { key: 'payment', label: '回款提醒（收到款 / 确认）' },
]

export default function NotificationSettingsPanel() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<NotificationSettings | null>(null)

  const query = useQuery({
    queryKey: ['notification-settings'],
    queryFn: getNotificationSettings,
  })

  // 服务端值到位后填一次表单；之后由用户编辑，不再被覆盖
  useEffect(() => {
    if (query.data && form === null) setForm(query.data)
  }, [query.data, form])

  const saveMutation = useMutation({
    mutationFn: (payload: NotificationSettings) => updateNotificationSettings(payload),
    onSuccess: (data, variables) => {
      Toast.success(variables.wecom_enabled ? '已保存' : '已保存（企微渠道已关闭）')
      setForm(data)
      void queryClient.invalidateQueries({ queryKey: ['notification-settings'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  if (query.isLoading || !form) {
    return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
  }

  const dirty = JSON.stringify(form) !== JSON.stringify(query.data)
  const wecomWarn = form.wecom_enabled && !form.wecom_agent_configured

  const setEvent = (key: keyof NotificationSettings['wecom_events'], value: boolean) => {
    setForm({ ...form, wecom_events: { ...form.wecom_events, [key]: value } })
  }

  return (
    <div style={{ display: 'grid', gap: 16, maxWidth: 720 }}>
      <SectionCard>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>通知渠道</div>
        <div style={{ display: 'grid', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Switch
              checked={form.inapp_enabled}
              onChange={(checked) => setForm({ ...form, inapp_enabled: checked })}
            />
            <div>
              <div style={{ fontSize: 14 }}>站内通知</div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                顶部铃铛里的通知，所有角色都看得到
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Switch
              checked={form.wecom_enabled}
              onChange={(checked) => setForm({ ...form, wecom_enabled: checked })}
            />
            <div>
              <div style={{ fontSize: 14 }}>企业微信通知</div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                通过企微应用发应用消息；需要成员已绑定企微 userid
              </div>
            </div>
          </div>
        </div>
        {!form.inapp_enabled && !form.wecom_enabled && (
          <Banner
            type="danger"
            closeIcon={null}
            style={{ marginTop: 12 }}
            description="两个渠道不能同时关闭，否则通知无处可发。"
          />
        )}
        {wecomWarn && (
          <Banner
            type="warning"
            closeIcon={null}
            style={{ marginTop: 12 }}
            description="企微渠道已开启，但后端还没配置企微应用（缺 WECOM_AGENT_ID）。当前投递会被标为「未投递」并写明原因，配好密钥后重启后端即可生效。"
          />
        )}
      </SectionCard>

      <SectionCard>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>企微通知范围</div>
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 12 }}>
          只有勾选的类型才会往企微发；站内通知始终全发。关掉某类可以避免企微被刷屏。
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          {EVENT_ROWS.map((row) => (
            <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Switch
                checked={form.wecom_events[row.key]}
                disabled={!form.wecom_enabled}
                onChange={(checked) => setEvent(row.key, checked)}
              />
              <span style={{ fontSize: 13 }}>{row.label}</span>
            </div>
          ))}
        </div>
      </SectionCard>

      <div style={{ display: 'flex', gap: 10 }}>
        <Button
          theme="solid"
          disabled={!dirty}
          loading={saveMutation.isPending}
          onClick={() => saveMutation.mutate(form)}
        >
          保存
        </Button>
        <Button disabled={!dirty} onClick={() => setForm(query.data ?? null)}>
          还原
        </Button>
      </div>
    </div>
  )
}
