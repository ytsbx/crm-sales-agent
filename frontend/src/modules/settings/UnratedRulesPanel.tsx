import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Switch, Toast } from '@douyinfe/semi-ui'
import { listPublicPoolRules, listTaskRules, saveUnratedRules } from '../../shared/api/settings'
import { UNRATED } from '../../shared/customer-level'

export default function UnratedRulesPanel() {
  const client = useQueryClient()
  const pools = useQuery({ queryKey: ['public-pool-rules'], queryFn: listPublicPoolRules })
  const tasks = useQuery({ queryKey: ['task-rules'], queryFn: listTaskRules })
  const [draft, setDraft] = useState<{
    reminderDays: string; recycleDays: string; reminderEnabled: boolean; recycleEnabled: boolean
  } | null>(null)
  const reminder = tasks.data?.find((row) => row.code === 'customer_unrated_silent')
  const recycle = pools.data?.find((row) => row.level === UNRATED)
  const current = draft ?? {
    reminderDays: reminder?.trigger_config?.days == null ? '' : String(reminder.trigger_config.days),
    recycleDays: recycle ? String(recycle.days) : '',
    reminderEnabled: reminder?.status === 'active', recycleEnabled: recycle?.enabled ?? false,
  }
  const { reminderDays, recycleDays, reminderEnabled, recycleEnabled } = current
  const mutation = useMutation({
    mutationFn: saveUnratedRules,
    onSuccess: async () => {
      Toast.success('未分级规则已保存')
      await Promise.all([client.invalidateQueries({ queryKey: ['public-pool-rules'] }), client.invalidateQueries({ queryKey: ['task-rules'] })])
      setDraft(null)
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const save = () => {
    const readDays = (raw: string, enabled: boolean, label: string) => {
      if (!raw.trim() && !enabled) return null
      const value = Number(raw)
      if (!Number.isInteger(value) || value < 1 || value > 36500) throw new Error(`${label}天数须为 1～36500 的整数`)
      return value
    }
    try {
      mutation.mutate({
        reminder_days: readDays(reminderDays, reminderEnabled, '跟进提醒'), reminder_enabled: reminderEnabled,
        recycle_days: readDays(recycleDays, recycleEnabled, '公海回收'), recycle_enabled: recycleEnabled,
      })
    } catch (error) { Toast.warning((error as Error).message) }
  }
  return <div>
    <div style={{ fontWeight: 600, marginBottom: 8 }}>未分级客户规则</div>
    <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 12 }}>
      新客户默认未分级。两项规则默认关闭，填写天数并保存后可启用；回收仍需预告与主管复核。
    </div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'center' }}>
      <label style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>未联系提醒
        <Input aria-label="未分级跟进提醒天数" placeholder="未设置天数" value={reminderDays} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, reminderDays: value })} style={{ width: 130 }} />天
        <Switch aria-label="启用未分级跟进提醒" checked={reminderEnabled} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, reminderEnabled: value })} />
      </label>
      <label style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>未跟进回收候选
        <Input aria-label="未分级公海回收天数" placeholder="未设置天数" value={recycleDays} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, recycleDays: value })} style={{ width: 130 }} />天
        <Switch aria-label="启用未分级公海回收" checked={recycleEnabled} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, recycleEnabled: value })} />
      </label>
      <Button onClick={save} loading={mutation.isPending} disabled={pools.isPending || tasks.isPending || pools.isError || tasks.isError}>保存未分级规则</Button>
    </div>
    {(pools.isError || tasks.isError) && <div role="alert">规则加载失败，请刷新后重试。</div>}
  </div>
}
