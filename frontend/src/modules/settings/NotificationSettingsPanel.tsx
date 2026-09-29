import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Select, Switch, Toast } from '@douyinfe/semi-ui'

import SectionCard from '../../shared/components/SectionCard'
import { getDeliveryFailures, retryFailedNotifications } from '../../shared/api/analytics'
import {
  getLevelPolicy,
  getNotificationSettings,
  updateLevelPolicy,
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
  // 领导口径的"过程可见"：报价提交 / 打样 / 下单自动留痕推业务主管。
  // 后端早就支持这一类，界面漏了开关——等于领导要么全收、要么一类都收不到
  { key: 'followup', label: '业务进展留痕（报价提交 / 打样 / 下单 → 推业务主管）' },
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

  // 投递失败概览 + 人工补投（文档 §六）：失败行不再是终点
  // 通知分级（场景24）：默认"全部即时推"，**不配就不改变投递行为**。
  // 哪些类型该进日报是业务决策，所以做成可配。
  const levelQuery = useQuery({ queryKey: ['level-policy'], queryFn: getLevelPolicy })
  const [policy, setPolicy] = useState<{ default_level: string; by_type: Record<string, string> } | null>(null)
  useEffect(() => {
    if (levelQuery.data) {
      setPolicy({
        default_level: levelQuery.data.default_level,
        by_type: levelQuery.data.by_type ?? {},
      })
    }
  }, [levelQuery.data])
  const levelMutation = useMutation({
    mutationFn: () => updateLevelPolicy(policy!),
    onSuccess: () => {
      Toast.success('分级策略已保存')
      void queryClient.invalidateQueries({ queryKey: ['level-policy'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const failuresQuery = useQuery({
    queryKey: ['delivery-failures'],
    queryFn: getDeliveryFailures,
  })
  const retryMutation = useMutation({
    mutationFn: () => retryFailedNotifications(),
    onSuccess: (data) => {
      Toast.success(
        data.requeued
          ? `补投 ${data.sent} 条，仍失败 ${data.failed} 条`
          : '没有需要补投的通知',
      )
      void queryClient.invalidateQueries({ queryKey: ['delivery-failures'] })
      void queryClient.invalidateQueries({ queryKey: ['notifications'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  if (query.isLoading || !form) {
    return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
  }

  const failures = failuresQuery.data
  const stuck = failures ? failures.failed + failures.skipped : 0

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

      {/* 场景24：主管一天收几十条业务事件时，逐条即时推等于把人训练成不看通知。
          这里只改「怎么推」，不改「能不能查」——站内通知永远逐条都在。 */}
      <SectionCard>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>通知分级（谁即时推、谁攒进日报）</div>
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 12, lineHeight: 1.7 }}>
          紧急项即时推送且**不进日报**（不让日报延误紧急事）；普通项即时推；
          日报项攒起来、每天合成一条发。**站内通知始终逐条都在**，分级只影响企微怎么推。
          默认全部即时推，不配置就等于维持现状。
        </div>
        {policy && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 13, width: 120 }}>默认（未指定类型）</span>
              <Select
                value={policy.default_level}
                onChange={(v) => setPolicy({ ...policy, default_level: v as string })}
                optionList={(levelQuery.data?.levels ?? []).map((l) => ({
                  value: l.value,
                  label: l.label,
                }))}
                style={{ width: 220 }}
              />
            </div>
            {EVENT_ROWS.map((row) => (
              <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 13, width: 120 }}>{row.label}</span>
                <Select
                  value={policy.by_type[row.key] ?? ''}
                  onChange={(v) =>
                    setPolicy({
                      ...policy,
                      by_type: { ...policy.by_type, [row.key]: v as string },
                    })
                  }
                  optionList={[
                    { value: '', label: '跟随默认' },
                    ...(levelQuery.data?.levels ?? []).map((l) => ({
                      value: l.value,
                      label: l.label,
                    })),
                  ]}
                  style={{ width: 220 }}
                />
              </div>
            ))}
            <div>
              <Button
                theme="solid"
                loading={levelMutation.isPending}
                onClick={() => levelMutation.mutate()}
              >
                保存分级策略
              </Button>
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>投递失败与补投</div>
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 12 }}>
          企微投递失败会保留业务记录并按退避自动重试（最多 {failures?.max_attempts ?? 3} 次
          {failures && !failures.auto_retry_enabled ? '，当前自动重试已关闭' : ''}
          ），超过后停在失败等人工处理。补投走的是原通知——不会重跑业务动作，
          也不会在客户时间线里多出一条记录。
        </div>
        {failuresQuery.isLoading ? (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap', marginBottom: 14 }}>
              {[
                { label: '待投递', value: failures?.pending ?? 0, hint: '等下一次投递' },
                {
                  label: '投递失败',
                  value: failures?.failed ?? 0,
                  hint: `${failures?.retrying ?? 0} 条会自动重试`,
                },
                {
                  label: '未投递',
                  value: failures?.skipped ?? 0,
                  hint: '对方未绑企微或渠道未配',
                },
                { label: '累计已投递', value: failures?.sent ?? 0, hint: '含补投成功' },
              ].map((stat) => (
                <div key={stat.label}>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{stat.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 600 }}>{stat.value}</div>
                  <div style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>{stat.hint}</div>
                </div>
              ))}
            </div>
            <Button
              loading={retryMutation.isPending}
              disabled={stuck === 0}
              onClick={() => retryMutation.mutate()}
            >
              补投失败与未投递的通知
            </Button>
          </>
        )}
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
