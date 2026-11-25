import { useMemo, useState } from 'react'
import { Banner, Button, Input, Modal, Select, TextArea, Toast } from '@douyinfe/semi-ui'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updatePaymentAllocations, type Payment, type Receivable } from '../../shared/api/order'
import { fillPaymentByDueDate, paymentCents, paymentMoney } from '../../shared/payment-allocation'
import { formatMoney } from '../../shared/components/money'
import FormLabel from '../../shared/components/FormLabel'
import GuardedButton from '../../shared/components/GuardedButton'

/** 当前分配 → 编辑用的 {期 id: 金额字符串}。 */
function initialAmounts(payment: Payment | null): Record<number, string> {
  const initial: Record<number, string> = {}
  for (const row of payment?.allocations ?? []) {
    initial[row.plan_id] = row.amount_exact ?? String(row.amount)
  }
  return initial
}

/**
 * 人工调整一笔回款的**分配**（第十批，主人 2026-10-13 拍板）。
 *
 * ## 它解决什么
 *
 * 一笔银行流水可以覆盖多期：超出的部分**按到期日从早到晚**自动抵后续期。
 * 但自动分配猜不出业务意图 —— 常见的两种情况要人来定：
 *
 *   · **挂错了期**：客户付的是二期的钱，登记时点成了一期；
 *   · **客户提前付款**：一次把后几期都付了，需要把这笔钱铺到对应几期上。
 *
 * 主人定的口径（原话）：
 *   「系统不能擅自调整已经登记的收款归属，必须由有权限的人员确认后才能调整。
 *     调整时要保留完整的操作审计记录，包括原归属期数、新归属期数、调整金额、
 *     操作人员和操作时间。」
 *
 * 所以这个弹窗做三件事：**让人看清现状 → 让人改成想要的 → 必须填原因**。
 * 原因不是形式：后端把它写进审计的 `after`，事后回答"当初为什么这么分"。
 *
 * ## 一个刻意的取舍：整份提交
 *
 * 提交时传的是**这笔回款的全部分配**（没传的期即不分配），后端按整份替换。
 * 这样审计里"原归属 → 新归属"是一条对照，而不是一串增删改 ——
 * 事后看审计的人不用自己重放操作序列。
 */
export default function AllocationAdjustModal({
  visible,
  payment,
  plans,
  currency,
  canEdit,
  onClose,
  onSaved,
}: {
  visible: boolean
  /** 要调整的那笔回款（带当前分配） */
  payment: Payment | null
  /** 本单的全部应收期（可分配的目标） */
  plans: Receivable[]
  currency?: string | null
  /** 有没有 `payment:manage`；没有就只能看 */
  canEdit: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const queryClient = useQueryClient()
  const [amounts, setAmounts] = useState<Record<number, string>>(() => initialAmounts(payment))
  const [reason, setReason] = useState('')

  const parsed = useMemo(() => {
    try {
      return { total: Object.values(amounts).reduce((sum, value) => sum + (value.trim() ? paymentCents(value) : 0n), 0n), valid: true }
    } catch { return { total: 0n, valid: false } }
  }, [amounts])
  const activePlans = plans.filter(plan => plan.status !== 'cancelled')
  const total = paymentMoney(parsed.total)
  const received = payment?.received_amount_exact ?? String(payment?.received_amount ?? 0)
  const receivedCents = paymentCents(received)
  const over = parsed.total > receivedCents
  const diff = paymentMoney(over ? 0n : receivedCents - parsed.total)
  /** 拦提交的原因（灰键必须说得出为什么 —— 项目口径） */
  const blockedReasons = useMemo(() => {
    const reasons: string[] = []
    if (!parsed.valid) reasons.push('分配金额必须是非负数，最多两位小数')
    if (!canEdit) reasons.push('只有持有「回款管理」权限的人才能调整分配')
    if (!payment || payment.status !== 'confirmed') {
      reasons.push('回款确认到账后才可以调整分配')
    }
    if (over) {
      reasons.push(
        '各期金额合计超过这笔回款，请减少分配金额',
      )
    }
    if (!reason.trim()) reasons.push('请填写调整原因')
    return reasons
  }, [canEdit, payment, over, reason, parsed.valid])

  /**
   * 「按当前应收自动铺一遍」：按到期日从早到晚填各期空缺 ——
   * 与后端自动分配的规则一致（`payment.allocation` 的规则），
   * 这样人按一下就能得到"系统会给的分法"，再在上面微调。
   */
  const fillByDueDate = () => {
    const { amounts: next } = fillPaymentByDueDate(received, plans, payment?.allocations ?? [])
    setAmounts(next)
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      updatePaymentAllocations(payment!.id, {
        allocations: Object.entries(amounts)
          .map(([planId, value]) => ({ plan_id: Number(planId), amount: value.trim() }))
          .filter((row) => row.amount && paymentCents(row.amount) > 0n),
        reason: reason.trim(),
      }),
    onSuccess: () => {
      Toast.success('回款分配已调整')
      queryClient.invalidateQueries({ queryKey: ['order-payments'] })
      queryClient.invalidateQueries({ queryKey: ['order-receivables'] })
      queryClient.invalidateQueries({ queryKey: ['order-milestones'] })
      queryClient.invalidateQueries({ queryKey: ['order-finance'] })
      queryClient.invalidateQueries({ queryKey: ['an-receivables'] })
      queryClient.invalidateQueries({ queryKey: ['an-payments'] })
      onSaved()
      onClose()
    },
  })

  const editing = canEdit && !saveMutation.isPending

  return (
    <Modal
      title="调整回款分配"
      className="payment-allocation-modal"
      visible={visible}
      onCancel={() => { if (!saveMutation.isPending) onClose() }}
      width={680}
      footer={
        <div className="workflow-modal-actions">
          <Button disabled={saveMutation.isPending} onClick={onClose}>取消</Button>
          <GuardedButton
            theme="outline"
            loading={saveMutation.isPending}
            disabled={blockedReasons.length > 0}
            blockedReasons={blockedReasons}
            action="保存分配"
            hintBelow
            onClick={() => saveMutation.mutate()}
          >
            保存分配
          </GuardedButton>
        </div>
      }
    >
      <div className="receivable-picker">
        <div>
          <strong>本笔到账 {formatMoney(received, currency)}</strong>
          {payment?.received_date && <span className="payment-allocation-meta"> · {payment.received_date}</span>}
          {payment?.plan_name && <span className="payment-allocation-meta"> · 登记期次 {payment.plan_name}</span>}
          <p className="receivable-picker-guide">填写这笔回款实际覆盖哪些期次。金额填 0 或留空表示这一期分不到这笔钱。</p>
        </div>

        <div className="payment-allocation-heading">
          <strong>各期分配</strong>
          {activePlans.length > 0 && (
            <Button size="small" theme="borderless" disabled={!editing} onClick={fillByDueDate}>按到期日建议分配</Button>
          )}
        </div>
        {activePlans.length === 0 && <p className="receivable-picker-guide">本单没有可分配的应收期。</p>}
        {activePlans.map(plan => {
          const current = (payment?.allocations ?? []).find(row => row.plan_id === plan.id)
          return (
            <section className="payment-allocation-plan" key={plan.id}>
              <div>
                <strong>{plan.plan_name}</strong>
                <div className="payment-allocation-meta">
                  应收 {formatMoney(plan.amount_exact ?? plan.amount, plan.currency)}
                  {' · '}累计已收 {formatMoney(plan.received_amount_exact ?? plan.received_amount, plan.currency)}
                  {plan.due_date && ` · 到期 ${plan.due_date}`}
                </div>
                <div>
                  本笔原分配 <strong>{formatMoney(current?.amount_exact ?? current?.amount ?? '0.00', currency)}</strong>
                  {current?.source === 'manual' && <span className="payment-allocation-meta"> · 人工确认</span>}
                </div>
              </div>
              <label className="payment-allocation-input">
                <span>调整后金额</span>
                <Input
                  aria-label={`${plan.plan_name}调整后金额`}
                  inputMode="decimal"
                  disabled={!editing}
                  value={amounts[plan.id] ?? ''}
                  placeholder="0.00"
                  onChange={value => setAmounts(previous => ({ ...previous, [plan.id]: value }))}
                />
              </label>
            </section>
          )
        })}

        <div className={`payment-allocation-total${!parsed.valid || over ? ' receivable-picker-unbalanced' : ''}`} aria-live="polite">
          {parsed.valid ? (
            <>
              <strong>已填分配 {formatMoney(total, currency)}</strong>
              {over
                ? <span>超出 {formatMoney(paymentMoney(parsed.total - receivedCents), currency)}</span>
                : parsed.total < receivedCents
                  ? <span>{formatMoney(diff, currency)} 尚未分配，保存后会显示为待分配</span>
                  : <span>本笔回款已分完</span>}
            </>
          ) : <span>请检查各期输入的金额</span>}
        </div>

        <div>
          <FormLabel required>调整原因</FormLabel>
          <Select
            aria-label="常见调整原因"
            style={{ width: '100%', marginBottom: 6 }}
            placeholder="选择常见原因，或在下方填写"
            disabled={!editing}
            value={undefined}
            onChange={value => setReason(String(value))}
            optionList={[
              { value: '回款登记的期与实际到账期不一致，改到正确的期', label: '登记的期挂错了' },
              { value: '客户提前支付了后续期款项，按客户指定分配到对应期', label: '客户提前付款' },
              { value: '按到期日重新分配，使各期结清情况与客户对账单一致', label: '对齐客户对账单' },
            ]}
          />
          <TextArea
            aria-label="调整原因"
            rows={2}
            value={reason}
            disabled={!editing}
            placeholder="例如：客户确认这笔钱实际是尾款"
            onChange={setReason}
          />
          <p className="receivable-picker-guide">调整前后的分配和填写的原因会一并留在操作记录里。</p>
        </div>

        {saveMutation.error && <Banner type="danger" fullMode={false} closeIcon={null} description={saveMutation.error.message} />}
      </div>
    </Modal>
  )
}
