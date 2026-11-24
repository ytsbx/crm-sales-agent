import { Button, DatePicker, Input, InputNumber, Radio } from '@douyinfe/semi-ui'

import { useId } from 'react'
import FormLabel from './FormLabel'
import GuardedButton from './GuardedButton'
import { formatMoney } from './money'
import { splitAmountsExact } from '../quote-todo'
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
  totalAmount?: number | string,
): string[] {
  if (mode === 'full') return []
  const reasons: string[] = []
  const ratioSum = rows.reduce((sum, row) => sum + (row.percent ?? 0), 0)
  if (Math.abs(ratioSum - 100) >= 0.01) {
    reasons.push(`比例合计现在是 ${Math.round(ratioSum * 100) / 100}%，必须是 100%`)
  }
  if (rows.length === 0) reasons.push('至少要有一期')
  rows.forEach((row, index) => {
    if (!row.name.trim()) reasons.push(`第 ${index + 1} 期还没填名称`)
    else if (Array.from(row.name.trim()).length > 64) reasons.push(`第 ${index + 1} 期名称最多 64 个字`)
    if (!row.due) reasons.push(`第 ${index + 1} 期还没选到期日`)
    if ((row.percent ?? 0) <= 0) reasons.push(`第 ${index + 1} 期的比例要大于 0`)
  })
  if (
    reasons.length === 0
    && totalAmount !== undefined
    && Number.isFinite(Number(totalAmount))
    && splitAmountsExact(totalAmount, rows.map((row) => (row.percent ?? 0) / 100)) === null
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
  totalAmount?: number | string,
): boolean {
  return planBlockedReasons(mode, rows, totalAmount).length === 0
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
  totalAmount?: number | string
  rows: ReceivablePlanRow[]
  onRowsChange: (rows: ReceivablePlanRow[]) => void
  mode: 'full' | 'installments'
  onModeChange?: (mode: 'full' | 'installments') => void
  currency?: string
}) {
  const dateLabelId = useId()
  const money = (value: number | string) => {
    const text = typeof value === 'string'
      ? formatMoney(value, 'CNY').slice(1)
      : (Number.isFinite(value) ? value : 0).toLocaleString('zh-CN', {
          minimumFractionDigits: 0, maximumFractionDigits: 2,
        })
    return `${currency === 'CNY' ? '¥' : ''}${text}`
  }

  const ratioSum = rows.reduce((sum, row) => sum + (row.percent ?? 0), 0)
  const ratioOk = Math.abs(ratioSum - 100) < 0.01
  const difference = Math.round(Math.abs(ratioSum - 100) * 100) / 100
  const hasTotal = totalAmount !== undefined && Number.isFinite(Number(totalAmount))
  // ⚠️ 预览金额**与后端同一套算法**（第九批问题 ⑤）。
  // 原来这里用 `Math.round`（JavaScript 浮点、半分向上），后端用
  // `Decimal`（默认 ROUND_HALF_EVEN）：`0.05` 分两期，页面显示 `0.03 / 0.02`、
  // 后端落库 `0.02 / 0.03` —— 用户确认的和存下去的不是一回事。
  // 现在统一走 `splitAmountsExact`，`ops/quote_todo.test.mjs` 用同一批
  // 用例盯着它与后端一致。
  const reasons = planBlockedReasons(mode, rows, totalAmount)
  const amounts = hasTotal && reasons.length === 0 && mode === 'installments'
    ? splitAmountsExact(
        totalAmount!,
        rows.map((row) => (row.percent ?? 0) / 100),
      )
    : null

  const update = (index: number, patch: Partial<ReceivablePlanRow>) => {
    const next = [...rows]
    next[index] = { ...next[index], ...patch }
    onRowsChange(next)
  }

  return (
    <div className="receivable-picker">
      {onModeChange && (
        <div>
          <FormLabel>收款方式</FormLabel>
          <Radio.Group
            aria-label="收款方式"
            value={mode}
            onChange={(event) => onModeChange(event.target.value as 'full' | 'installments')}
          >
            <Radio value="full">一次收全款</Radio>
            <Radio value="installments">分期收款</Radio>
          </Radio.Group>
        </div>
      )}

      {mode === 'full' ? (
        <p className="receivable-picker-guide">
          生成一条全款应收{hasTotal ? `（${money(totalAmount!)}）` : ''}。
          有计划交期时以交期为到期日，否则默认在下单 30 天后到期。可在订单详情调整或拆分。
        </p>
      ) : (
        <>
          <div className="receivable-picker">
            {rows.map((row, index) => (
              <section key={index} className="receivable-installment" aria-label={`第 ${index + 1} 期`}>
                <div className="receivable-installment-heading">
                  <strong>第 {index + 1} 期</strong>
                  {amounts && <span className="receivable-installment-amount">{money(amounts[index])}</span>}
                  {rows.length > 1 && (
                    <Button
                      size="small"
                      theme="borderless"
                      type="tertiary"
                      aria-label={`移除第 ${index + 1} 期`}
                      onClick={() => onRowsChange(rows.filter((_, i) => i !== index))}
                    >
                      移除
                    </Button>
                  )}
                </div>
                <div className="receivable-installment-fields">
                  <div>
                    <FormLabel>名称</FormLabel>
                    <Input
                      aria-label={`第 ${index + 1} 期名称`}
                      value={row.name}
                      placeholder={`第 ${index + 1} 期`}
                      onChange={(value) => update(index, { name: value })}
                    />
                  </div>
                  <div>
                    <FormLabel>比例（%）</FormLabel>
                    <InputNumber
                      style={{ width: '100%' }}
                      aria-label={`第 ${index + 1} 期比例`}
                      value={row.percent ?? undefined}
                      min={0}
                      max={100}
                      precision={2}
                      placeholder="如 30"
                      onChange={(value) => update(index, { percent: typeof value === 'number' ? value : null })}
                    />
                  </div>
                  <div className="receivable-installment-date">
                    <FormLabel><span id={`${dateLabelId}-${index}`}>到期日</span></FormLabel>
                    <DatePicker
                      style={{ width: '100%' }}
                      aria-labelledby={`${dateLabelId}-${index}`}
                      value={row.due ?? undefined}
                      onChange={(date) => update(index, { due: (date as Date) ?? null })}
                    />
                  </div>
                </div>
              </section>
            ))}
          </div>
          <div className="receivable-picker-summary">
            <GuardedButton
              size="small"
              disabled={rows.length >= 24}
              blockedReasons={['最多可设置 24 期。']}
              onClick={() => onRowsChange([
                ...rows,
                { name: `第 ${rows.length + 1} 期`, percent: null, due: new Date() },
              ])}
            >
              再加一期
            </GuardedButton>
            <span className={ratioOk ? undefined : 'receivable-picker-unbalanced'}>
              合计 {Math.round(ratioSum * 100) / 100}%
              {!ratioOk && ` · ${ratioSum < 100 ? '还差' : '超过'} ${difference}%`}
            </span>
          </div>
          {rows.length > 1 && <p className="receivable-picker-guide">最后一期按剩余金额计算，确保各期金额加起来等于订单总额。</p>}
          {!hasTotal && <p className="receivable-picker-guide">各期金额会按成交报价的订单总额计算。</p>}
        </>
      )}
    </div>
  )
}
