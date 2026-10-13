import { useState } from 'react'
import { Button, DatePicker, Input, InputNumber, Radio } from '@douyinfe/semi-ui'

import BlockedHint from './BlockedHint'
import { splitAmountsLikeBackend } from '../quote-todo'
import { calendarDayOrToday } from '../calendar-day'

/**
 * 转单时的「收款计划」选择件（2026-10-13 改动三）。
 *
 * ## 为什么要有它
 *
 * 从前转单是**写死一条「全款」**，想拆定金/尾款得转完单再去订单详情页
 * 删掉那条、再按比例重新生成 —— 主人原话是"转单时直接选付款方式"。
 * 现在在转单确认这一步就能定下来。
 *
 * ## 口径（主人 2026-10-13 确认）
 *
 * - **默认「全款」**：不动老习惯。全款 **就是**不分期 —— 一条覆盖全额、
 *   到期日取交期，不是一个额外的第三选项。
 * - 选「分期」才展开行编辑：默认两行（定金 30% / 尾款 70%），
 *   名称、比例、到期日都能改，也能加到 24 期。
 * - 不给"一条都不建"的选项：那样忘建就没人催款（项目里"建单靠记性建应收"
 *   正是当初要避免的）。
 * - 比例合计必须**正好 100%**，否则组件自己就把提交拦下并说清差多少。
 *   金额预览与后端同口径（前期四舍五入到分、**末期补差额**），
 *   各期之和严格等于订单总额 —— 否则用户自己加一遍会以为差了一分钱。
 *
 * ## 与订单详情页的关系
 *
 * 订单详情页那个「生成应收计划」弹窗是同一个交互，但它是**订单已存在**之后
 * 的事情；这里是**转单那一刻**。两者共用同一套后端判据
 * （`OrderFromQuote.receivable_plan` 复用 `ReceivableGenerate` 的校验），
 * 所以不会出现"这里能填、那里不能填"。
 */

export interface ReceivablePlanRow {
  name: string
  percent: number | null
  due: Date | null
}

/** 提交给后端的形状：`ratios` 与 `due_dates`/`names` 按下标对齐。 */
export interface ReceivablePlanPayload {
  ratios: number[]
  due_dates: string[]
  names: string[]
}

// 日历日期用共用的按当地年月日取（第九批问题 2）：原来这里的 UTC 截日
// 会把当地零点的日期算成前一天。
const iso = (value: Date | null) => calendarDayOrToday(value)

export const defaultPlanRows = (): ReceivablePlanRow[] => [
  { name: '定金', percent: 30, due: new Date() },
  { name: '尾款', percent: 70, due: new Date() },
]

/** 把行转成后端入参。`mode==='full'` 时返回 null，表示"不传 = 一条全款"。 */
export function toPlanPayload(
  mode: 'full' | 'installments',
  rows: ReceivablePlanRow[],
): ReceivablePlanPayload | null {
  if (mode === 'full') return null
  return {
    ratios: rows.map((row) => (row.percent ?? 0) / 100),
    due_dates: rows.map((row) => iso(row.due)),
    names: rows.map((row, index) => row.name.trim() || `第 ${index + 1} 期`),
  }
}

/**
 * 这一刻"为什么还不能提交"。**空数组 = 可以提交**。
 *
 * 调用方把它接到 `BlockedHint` 上显示在弹窗页脚（项目口径：灰键必须说得出为什么；
 * `ops/check_disabled_hint.py` 会检查每个含禁用控件的文件都接了共用件）。
 * 判据只此一处 —— 组件内部的提示与页脚的提示必须同源，否则两边会说法不一。
 */
export function planBlockedReasons(
  mode: 'full' | 'installments',
  rows: ReceivablePlanRow[],
  totalAmount?: number,
): string[] {
  if (mode === 'full') return []
  const reasons: string[] = []
  const ratioSum = rows.reduce((sum, row) => sum + (row.percent ?? 0), 0)
  if (Math.abs(ratioSum - 100) >= 0.01) {
    reasons.push(`比例合计现在是 ${Math.round(ratioSum * 100) / 100}%，必须是 100%`)
  }
  if (rows.length === 0) reasons.push('至少要有一期')
  rows.forEach((row, index) => {
    if (!row.name.trim()) reasons.push(`第 ${index + 1} 期还没填节点名`)
    if (!row.due) reasons.push(`第 ${index + 1} 期还没选到期日`)
    if ((row.percent ?? 0) <= 0) reasons.push(`第 ${index + 1} 期的比例要大于 0`)
  })
  if (
    reasons.length === 0
    && typeof totalAmount === 'number'
    && Number.isFinite(totalAmount)
    && splitAmountsLikeBackend(totalAmount, rows.map((row) => (row.percent ?? 0) / 100)) === null
  ) {
    // 金额太小：前面各期四舍五入到分之后，末期会变成 0 或负数
    reasons.push(
      `订单总额 ${totalAmount} 按当前比例分 ${rows.length} 期会分出不大于 0 的期，`
      + '请减少期数或调整比例',
    )
  }
  return reasons
}

/** 这一刻的计划能不能提交。调用方用它把确认键一起禁掉（灰键必须说得出为什么）。 */
export function isPlanValid(
  mode: 'full' | 'installments',
  rows: ReceivablePlanRow[],
  totalAmount?: number,
): boolean {
  if (mode === 'full') return true
  if (rows.length === 0) return false
  const ratioSum = rows.reduce((sum, row) => sum + (row.percent ?? 0), 0)
  if (Math.abs(ratioSum - 100) >= 0.01) return false
  if (!rows.every((row) => row.name.trim() && row.due && (row.percent ?? 0) > 0)) return false
  // 金额太小时会分出"不大于 0 的期"（后端同样会 422）：
  // 与其让用户点了再报错，不如在这里就拦住并说明（第九批问题 ①⑤）
  if (typeof totalAmount === 'number' && Number.isFinite(totalAmount)) {
    const split = splitAmountsLikeBackend(totalAmount, rows.map((row) => (row.percent ?? 0) / 100))
    if (split === null) return false
  }
  return true
}

export default function ReceivablePlanPicker({
  totalAmount,
  rows,
  onRowsChange,
  mode,
  onModeChange,
  currency = 'CNY',
}: {
  /**
   * 订单总额，用来算每期金额预览。
   *
   * ⚠️ **可能没有**：商机成交那条入口，成交时转的是"最新已发送/已接受的报价版本"
   * （由后端挑），前端此刻并不知道是哪一份、金额多少。那种情况下**别拿
   * 「预计金额」凑**（那是估算，不是订单金额）—— 传 undefined，
   * 组件只显示比例、不显示金额，并说明"金额在转单时按报价版本确定"。
   */
  totalAmount?: number
  rows: ReceivablePlanRow[]
  onRowsChange: (rows: ReceivablePlanRow[]) => void
  mode: 'full' | 'installments'
  onModeChange: (mode: 'full' | 'installments') => void
  currency?: string
}) {
  const [touched, setTouched] = useState(false)
  const money = (value: number) =>
    `${currency === 'CNY' ? '¥' : ''}${(Number.isFinite(value) ? value : 0).toLocaleString('zh-CN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    })}`

  const ratioSum = rows.reduce((sum, row) => sum + (row.percent ?? 0), 0)
  const ratioOk = Math.abs(ratioSum - 100) < 0.01
  const hasTotal = typeof totalAmount === 'number' && Number.isFinite(totalAmount)
  // ⚠️ 预览金额**与后端同一套算法**（第九批问题 ⑤）。
  // 原来这里用 `Math.round`（JavaScript 浮点、半分向上），后端用
  // `Decimal`（默认 ROUND_HALF_EVEN）：`0.05` 分两期，页面显示 `0.03 / 0.02`、
  // 后端落库 `0.02 / 0.03` —— 用户确认的和存下去的不是一回事。
  // 现在统一走 `splitAmountsLikeBackend`，`ops/quote_todo.test.mjs` 用同一批
  // 用例盯着它与后端一致。
  const split = hasTotal
    ? splitAmountsLikeBackend(
        totalAmount!,
        rows.map((row) => (row.percent ?? 0) / 100),
      )
    : null
  const amounts: number[] = split ?? rows.map(() => 0)

  const update = (index: number, patch: Partial<ReceivablePlanRow>) => {
    const next = [...rows]
    next[index] = { ...next[index], ...patch }
    onRowsChange(next)
  }

  // 与调用方显示的"为什么不能提交"同源（见 `planBlockedReasons`）
  const reasons = planBlockedReasons(mode, rows)

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div>
        <div style={{ marginBottom: 4 }}>收款计划</div>
        <Radio.Group
          value={mode}
          onChange={(event) => {
            setTouched(true)
            onModeChange(event.target.value as 'full' | 'installments')
          }}
        >
          <Radio value="full">全款（一条应收，到期日取交期）</Radio>
          <Radio value="installments">分期（拆成多期，比例与到期日自己定）</Radio>
        </Radio.Group>
      </div>

      {mode === 'full' ? (
        <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
          转单后生成 <b>1 条</b>应收：金额 = 订单总额
          {hasTotal ? `（${money(totalAmount!)}）` : '（转单时按报价版本确定）'}，到期日取订单交期。
          之后在订单详情页也能调整或拆分。
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--crm-text-3)' }}>
              <div style={{ width: 140 }}>节点名</div>
              <div style={{ width: 100 }}>比例（%）</div>
              <div style={{ flex: 1 }}>到期日</div>
              {hasTotal && <div style={{ width: 100, textAlign: 'right' }}>金额</div>}
              <div style={{ width: 44 }} />
            </div>
            {rows.map((row, index) => (
              <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Input
                  style={{ width: 140 }}
                  value={row.name}
                  placeholder={`第 ${index + 1} 期`}
                  onChange={(value) => update(index, { name: value })}
                />
                <InputNumber
                  style={{ width: 100 }}
                  value={row.percent ?? undefined}
                  min={0}
                  max={100}
                  precision={2}
                  placeholder="如 30"
                  onChange={(value) => update(index, { percent: typeof value === 'number' ? value : null })}
                />
                <div style={{ flex: 1 }}>
                  <DatePicker
                    style={{ width: '100%' }}
                    value={row.due ?? undefined}
                    onChange={(date) => update(index, { due: (date as Date) ?? null })}
                  />
                </div>
                {hasTotal && <div style={{ width: 100, textAlign: 'right' }}>{money(amounts[index])}</div>}
                <div style={{ width: 44, textAlign: 'right' }}>
                  {rows.length > 1 && (
                    <a
                      style={{ color: 'var(--crm-text-3)' }}
                      onClick={() => onRowsChange(rows.filter((_, i) => i !== index))}
                    >
                      删掉
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Button
              size="small"
              disabled={rows.length >= 24}
              onClick={() => {
                setTouched(true)
                onRowsChange([
                  ...rows,
                  { name: `第 ${rows.length + 1} 期`, percent: null, due: new Date() },
                ])
              }}
            >
              再加一期
            </Button>
            <span style={{ fontSize: 13, color: ratioOk ? 'var(--crm-text-2)' : 'var(--crm-error, #ba1a1a)' }}>
              合计比例 {Math.round(ratioSum * 100) / 100}%{ratioOk ? '' : '（必须正好 100%）'}
            </span>
            {/* 没有订单总额时不编数字：宁可只说比例，也不拿"预计金额"冒充订单金额 */}
            {!hasTotal && (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                各期金额在转单时按报价版本总额自动算（前期抹零、末期补差，合计=订单总额）
              </span>
            )}
          </div>
          {/* 用**共用件**而不是自己写一句（项目口径：灰键的说明走同一套渲染，
              也让 `ops/check_disabled_hint.py` 认得出来）。
              只在用户动过之后才提示，免得刚展开就一片红。 */}
          {touched && <BlockedHint reasons={reasons} action="提交" />}
        </>
      )}
    </div>
  )
}
