import { useState } from 'react'
import { IconCalendarClock, IconComment, IconUser } from '@douyinfe/semi-icons'
import { Button, Input, Popconfirm } from '@douyinfe/semi-ui'
import { Link } from 'react-router-dom'

import type { TaskRuleRow } from '../../shared/api/settings'
import './AutoTaskRulesPanel.css'

const ruleCopy = {
  quote_no_followup: {
    icon: IconComment,
    label: '报价发出后等几天',
    detail: '报价仍在等回复，且这段时间没有跟进记录时，提醒报价负责人。',
  },
  customer_silent: {
    icon: IconUser,
    label: '多久没联系就提醒',
    detail: '重点客户一直没有跟进或业务进展时，提醒客户负责人。已约好未来跟进时间的客户会跳过。',
  },
  receivable_due: {
    icon: IconCalendarClock,
    label: '到期前提前几天',
    detail: '应收将到期或已逾期，且款项还没收齐时，提醒订单负责人。',
  },
} as const

function TaskRuleItem({ rule, saving, onSave }: {
  rule: TaskRuleRow
  saving: boolean
  onSave: (id: number, days: number) => Promise<unknown>
}) {
  const saved = String(rule.trigger_config?.days ?? 3)
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? saved
  const days = Number(text)
  const valid = /^\d+$/.test(text.trim()) && Number.isSafeInteger(days)
  const dirty = draft !== null && text.trim() !== saved
  const copy = ruleCopy[rule.trigger_type as keyof typeof ruleCopy]
  const Icon = copy?.icon ?? IconCalendarClock
  const active = rule.status === 'active'
  const inputId = `auto-task-days-${rule.id}`

  const save = async () => {
    if (!dirty || !valid || saving) return
    try {
      await onSave(rule.id, days)
      setDraft(null)
    } catch {
      // SettingsPage shows the error and the draft stays available for retry.
    }
  }

  return (
    <li className="auto-task-rule">
      <span className="auto-task-rule-icon" aria-hidden="true"><Icon size="large" /></span>
      <div className="auto-task-rule-copy">
        <div className="auto-task-rule-title">
          <h4>{rule.name}</h4>
          <span className={`auto-task-rule-status${active ? ' is-active' : ''}`}>
            {active ? '已启用' : '已停用'}
          </span>
        </div>
        {copy && <p>{copy.detail}</p>}
      </div>
      <div className="auto-task-rule-field">
        <label htmlFor={inputId}>{copy?.label ?? '提醒天数'}</label>
        <div className="auto-task-rule-edit">
          <Input id={inputId} inputMode="numeric" suffix="天" value={text} disabled={saving}
            aria-invalid={dirty && !valid}
            onChange={setDraft} onEnterPress={() => void save()} />
          <Button type="primary" theme="outline" disabled={!dirty || !valid || saving}
            onClick={() => void save()}>保存</Button>
        </div>
        {dirty && !valid && <p className="auto-task-rule-error" role="status">请输入 0 或更大的整数。</p>}
      </div>
    </li>
  )
}

export default function AutoTaskRulesPanel({ rules, pending, failed, saving, scanning, onReload, onSave, onScan }: {
  rules: TaskRuleRow[]
  pending: boolean
  failed: boolean
  saving: boolean
  scanning: boolean
  onReload: () => void
  onSave: (id: number, days: number) => Promise<unknown>
  onScan: () => void
}) {
  return (
    <section className="auto-task-panel" aria-labelledby="auto-task-heading">
      <header className="auto-task-heading">
        <h3 id="auto-task-heading">自动待办</h3>
        <p>系统每天检查已启用的规则，符合条件时生成内部待办。</p>
      </header>
      {pending ? <p className="auto-task-empty">正在读取规则…</p> : failed ? (
        <div className="auto-task-empty">无法读取自动任务规则。 <Button onClick={onReload}>重新读取</Button></div>
      ) : rules.length ? (
        <ul className="auto-task-rule-list">
          {rules.map((rule) => <TaskRuleItem key={rule.id} rule={rule} saving={saving} onSave={onSave} />)}
        </ul>
      ) : <p className="auto-task-empty">暂无自动任务规则。</p>}
      <footer className="auto-task-footer">
        <div>
          <h4>现在检查一次</h4>
          <p>按已保存的规则扫描。已有同类待办时会跳过。</p>
        </div>
        <div className="auto-task-footer-actions">
          <Link to="/tasks">查看任务中心</Link>
          <Popconfirm title="确认立即扫描自动待办？"
            content="系统会按已保存、已启用的规则生成待办。"
            onConfirm={onScan} disabled={saving || scanning}>
            <Button type="primary" theme="outline" loading={scanning} disabled={saving}>立即扫描</Button>
          </Popconfirm>
        </div>
      </footer>
    </section>
  )
}
