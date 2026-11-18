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
  // 跟业务规则页其它 5 块统一：白底描边卡 + 主色竖条标题（见 index.css 的 .rule-block）
  return <div className="rule-block">
    <div className="rule-block-title">未分级客户规则</div>
    <div style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.65, marginBottom: 14 }}>
      新客户默认未分级。两项规则默认关闭，填写天数并保存后可启用；回收仍需预告与主管复核。
    </div>
    {/* 两条规则原来并排挤在一行：输入框、单位「天」、开关、保存按钮全连成一条，
        读者要靠位置猜"这个开关是管哪条的"。改成两行，一行一条规则，
        名称固定 200px 宽，输入和开关从同一列开始对齐；
        保存按钮单独一行靠右 —— 它是这一块的总操作，不该挤在规则中间。 */}
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="unrated-rule-row">
        <span className="unrated-rule-name">未联系提醒</span>
        <Input aria-label="未分级跟进提醒天数" placeholder="未设置天数" value={reminderDays} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, reminderDays: value })} style={{ width: 130, flex: '0 0 130px' }} />
        <span style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>天</span>
        <Switch aria-label="启用未分级跟进提醒" checked={reminderEnabled} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, reminderEnabled: value })} />
        <span className="unrated-rule-note">开启后，超过这个天数没联系的未分级客户会进提醒</span>
      </div>
      <div className="unrated-rule-row">
        <span className="unrated-rule-name">未跟进回收候选</span>
        <Input aria-label="未分级公海回收天数" placeholder="未设置天数" value={recycleDays} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, recycleDays: value })} style={{ width: 130, flex: '0 0 130px' }} />
        <span style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>天</span>
        <Switch aria-label="启用未分级公海回收" checked={recycleEnabled} disabled={mutation.isPending} onChange={(value) => setDraft({ ...current, recycleEnabled: value })} />
        <span className="unrated-rule-note">开启后，超过这个天数没跟进会被提单为回收候选</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
        <Button onClick={save} loading={mutation.isPending} disabled={pools.isPending || tasks.isPending || pools.isError || tasks.isError}>保存未分级规则</Button>
      </div>
    </div>
    {(pools.isError || tasks.isError) && <div role="alert">规则加载失败，请刷新后重试。</div>}
  </div>
}
