import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Popconfirm, Select, Switch, Toast } from '@douyinfe/semi-ui'
import { IconBell, IconHistogram, IconSend } from '@douyinfe/semi-icons'

import { getDeliveryFailures, retryFailedNotifications } from '../../shared/api/analytics'
import {
  getLevelPolicy,
  getNotificationSettings,
  updateLevelPolicy,
  updateNotificationSettings,
  type NotificationLevelPolicy,
  type NotificationSettings,
} from '../../shared/api/notificationSettings'

const EVENT_ROWS: { key: keyof NotificationSettings['wecom_events']; label: string }[] = [
  { key: 'approval', label: '审批提醒' },
  { key: 'task', label: '任务提醒' },
  { key: 'payment', label: '回款提醒' },
  { key: 'followup', label: '业务过程通知（发给主管）' },
]

type LevelDraft = Pick<NotificationLevelPolicy, 'default_level' | 'by_type'>

const DELIVERY_OPTIONS = [
  { value: 'normal', label: '逐条即时推送' },
  { value: 'digest', label: '每天汇总发送' },
]

export default function NotificationSettingsPanel() {
  const client = useQueryClient()
  const [channelDraft, setChannelDraft] = useState<NotificationSettings | null>(null)
  const [levelDraft, setLevelDraft] = useState<LevelDraft | null>(null)

  const channelsQuery = useQuery({ queryKey: ['notification-settings'], queryFn: getNotificationSettings })
  const levelsQuery = useQuery({ queryKey: ['level-policy'], queryFn: getLevelPolicy })
  const failuresQuery = useQuery({ queryKey: ['delivery-failures'], queryFn: getDeliveryFailures })

  const appliedChannels = channelsQuery.data
  const channels = channelDraft ?? appliedChannels
  const levels = levelDraft ?? levelsQuery.data
  const failures = failuresQuery.data
  const undelivered = failures ? failures.failed + failures.skipped : 0
  const retrying = failures?.retrying ?? 0
  const pendingEvents = failures?.business_pending ?? 0

  const hasChanges = Boolean(channelDraft || levelDraft)
  const hasNoChannel = Boolean(channels && !channels.inapp_enabled && !channels.wecom_enabled)
  const saveSettings = useMutation({
    mutationFn: async () => {
      if (levelDraft) {
        const by_type = { ...levelDraft.by_type }
        // 先保存发送方式，再开启渠道，避免新通知按旧方式发送。
        for (const { key } of EVENT_ROWS) by_type[key] ??= levelDraft.default_level
        const saved = await updateLevelPolicy({ ...levelDraft, by_type }).catch(() => {
          throw new Error('发送方式未保存，请重试。')
        })
        client.setQueryData<NotificationLevelPolicy>(['level-policy'], (previous) => previous && { ...previous, ...saved })
        setLevelDraft(null)
      }
      if (channelDraft) {
        const { inapp_enabled, wecom_enabled, wecom_events } = channelDraft
        const saved = await updateNotificationSettings({ inapp_enabled, wecom_enabled, wecom_events }).catch(() => {
          throw new Error(levelDraft ? '发送方式已保存，通知渠道未保存。请重试。' : '通知渠道未保存，请重试。')
        })
        client.setQueryData(['notification-settings'], saved)
        setChannelDraft(null)
        void client.invalidateQueries({ queryKey: ['delivery-failures'] })
      }
    },
    onSuccess: () => Toast.success('通知设置已保存'),
    onError: (error: Error) => Toast.error(error.message),
  })
  const retryDelivery = useMutation({
    mutationFn: () => retryFailedNotifications(),
    onSuccess: (result) => {
      const retrySummary = result.requeued
        ? `企微补发结果：已送达 ${result.sent} 条，发送失败 ${result.failed} 条，跳过 ${result.skipped} 条。`
        : '没有需要补发的企微通知。'
      const events = result.business_events
      const message = events?.processed || events?.failed
        ? `已生成主管通知 ${events.notifications} 条，生成失败 ${events.failed} 项。${retrySummary}`
        : retrySummary
      if (result.failed || result.skipped || events?.failed) Toast.warning(message)
      else Toast.success(message)
      void client.invalidateQueries({ queryKey: ['delivery-failures'] })
      void client.invalidateQueries({ queryKey: ['notifications'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div className="settings-notifications">
      <section className="settings-notice-card settings-notice-main">
        <header className="settings-notice-header">
          <h4><IconBell aria-hidden="true" />通知设置</h4>
          <div className="settings-notice-actions">
            {hasChanges && (
              <Button theme="borderless" disabled={saveSettings.isPending} onClick={() => {
                setChannelDraft(null)
                setLevelDraft(null)
              }}>放弃修改</Button>
            )}
            <Button theme="solid" type="primary" loading={saveSettings.isPending}
              disabled={!hasChanges || hasNoChannel} onClick={() => saveSettings.mutate()}>保存设置</Button>
          </div>
        </header>
        {channelsQuery.isError ? (
          <Button onClick={() => void channelsQuery.refetch()}>重新读取通知渠道</Button>
        ) : !channels ? <p className="settings-help">正在读取通知渠道…</p> : (
          <>
            <div className="settings-notice-channels">
              <div className="settings-control-row">
                <span>站内通知 <small>在 CRM 顶部铃铛查看</small></span>
                <Switch aria-label="站内通知" checked={channels.inapp_enabled} disabled={saveSettings.isPending}
                  onChange={(checked) => setChannelDraft({ ...channels, inapp_enabled: checked })} />
              </div>
              <div className="settings-control-row">
                <span>企业微信通知 <small>发给已绑定企微的成员</small></span>
                <Switch aria-label="企业微信通知" checked={channels.wecom_enabled} disabled={saveSettings.isPending}
                  onChange={(checked) => setChannelDraft({ ...channels, wecom_enabled: checked })} />
              </div>
            </div>
            {channels.wecom_enabled && !channels.wecom_ready && (
              <Banner type="warning" closeIcon={null} description="企业微信对接尚未就绪，当前无法发送企微通知。" />
            )}
            {hasNoChannel && (
              <Banner type="danger" closeIcon={null} description="请至少保留一个通知渠道。" />
            )}
          </>
        )}

        <h4 className="settings-notice-heading"><IconSend aria-hidden="true" />企微提醒</h4>
        {levelsQuery.isError && <Button onClick={() => void levelsQuery.refetch()}>重新读取发送方式</Button>}
        {!levels && !levelsQuery.isError && <p className="settings-help">正在读取发送方式…</p>}

        <table className="settings-notice-events">
          <thead><tr><th scope="col">提醒类型</th><th scope="col">发到企微</th><th scope="col">发送方式</th></tr></thead>
          <tbody>
            {EVENT_ROWS.map(({ key, label }) => (
              <tr key={key}>
                <th scope="row" id={`notice-level-${key}`}>{label}</th>
                <td>
                  {channels && !channelsQuery.isError && (
                    <Switch aria-label={`${label}发到企微`} checked={channels.wecom_events[key]}
                      disabled={!channels.wecom_enabled || saveSettings.isPending}
                      onChange={(checked) => setChannelDraft({ ...channels, wecom_events: { ...channels.wecom_events, [key]: checked } })} />
                  )}
                </td>
                <td>
                  {levels && !levelsQuery.isError && (
                    <Select aria-labelledby={`notice-level-${key}`} className="settings-notice-select"
                      value={(levels.by_type[key] ?? levels.default_level) === 'digest' ? 'digest' : 'normal'}
                      disabled={saveSettings.isPending} optionList={DELIVERY_OPTIONS}
                      onChange={(value) => {
                        if (value === 'normal' || value === 'digest') {
                          setLevelDraft({ default_level: levels.default_level, by_type: { ...levels.by_type, [key]: value } })
                        }
                      }} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {channels && !channels.wecom_enabled && <p className="settings-help">开启企业微信通知后，可选择要发到企微的提醒。</p>}
        {channels?.wecom_enabled && !EVENT_ROWS.some(({ key }) => channels.wecom_events[key]) && (
          <p className="settings-help">这四类提醒都未开启，不会新发到企业微信。</p>
        )}
      </section>

      <aside className="settings-notice-card settings-notice-side">
        <section className="settings-delivery-card">
          <h4><IconHistogram aria-hidden="true" />企微送达情况</h4>
          <p className="settings-help">这里统计企业微信推送。站内通知可在 CRM 顶部铃铛查看。</p>
          {channelsQuery.isError ? (
            <div style={{ display: 'grid', gap: 6, justifyItems: 'start' }}>
              <p className="settings-help" style={{ margin: 0 }}>暂时无法读取通知渠道。</p>
              <Button
                size="small"
                theme="borderless"
                style={{ paddingLeft: 0 }}
                onClick={() => void channelsQuery.refetch()}
              >
                重新读取通知渠道
              </Button>
            </div>
          ) : !appliedChannels ? <p className="settings-help">正在读取通知渠道…</p>
            : !appliedChannels.wecom_enabled ? (
              <>
                <p className="settings-delivery-status">
                  {channelDraft?.wecom_enabled ? '保存设置后启用企微通知。' : '企业微信通知当前已关闭。'}
                </p>
                {pendingEvents > 0 && <p className="settings-help">另有 {pendingEvents} 项主管通知待生成。</p>}
              </>
            )
            : failuresQuery.isError ? (
              <Button onClick={() => void failuresQuery.refetch()}>重新读取送达情况</Button>
            ) : !failures ? <p className="settings-help">正在读取送达情况…</p> : (
            <>
              {undelivered > 0 ? (
                <>
                  <p className="settings-delivery-status">有 {undelivered} 条企微通知未送达</p>
                  <dl className="settings-delivery-counts">
                    <div><dt>发送失败</dt><dd>{failures.failed} 条</dd></div>
                    <div><dt>已跳过</dt><dd>{failures.skipped} 条</dd></div>
                  </dl>
                  <p className="settings-help">
                    {failures.auto_retry_enabled === false ? '自动重试已关闭。' : retrying > 0
                      ? `目前有 ${retrying} 条失败通知已到重试时间，等待系统处理。`
                      : '目前没有到重试时间的失败通知。'}
                    {failures.skipped > 0 && '未绑定企微、账号停用或推送未配置都可能导致跳过。'}
                  </p>
                </>
              ) : <p className="settings-delivery-status">当前没有发送失败或已跳过的企微通知。</p>}
              {pendingEvents > 0 && <p className="settings-help">另有 {pendingEvents} 项主管通知待生成。</p>}
              {(undelivered > 0 || pendingEvents > 0) && (
                <div className="settings-action-row">
                  <Popconfirm title="确认重新处理这些通知？"
                    content="系统会先处理待生成的主管通知，再尝试补发此前失败或跳过的企微通知，每次最多各处理 200 条。"
                    onConfirm={() => retryDelivery.mutate()}>
                    <Button theme="outline" loading={retryDelivery.isPending}>{undelivered > 0 ? '补发企微通知' : '处理主管通知'}</Button>
                  </Popconfirm>
                </div>
              )}
            </>
          )}
        </section>
      </aside>
    </div>
  )
}
