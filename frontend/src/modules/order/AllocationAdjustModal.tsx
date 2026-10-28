import { useMemo, useState } from 'react'
import { Button, Input, Modal, Select, TextArea, Toast } from '@douyinfe/semi-ui'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updatePaymentAllocations, type Payment, type Receivable } from '../../shared/api/order'
import { fillPaymentByDueDate, paymentCents, paymentMoney } from '../../shared/payment-allocation'
import { formatMoney } from '../../shared/components/money'
import BlockedHint from '../../shared/components/BlockedHint'
import FormLabel from '../../shared/components/FormLabel'

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
/** 当前分配 → 编辑用的 {期 id: 金额字符串}。 */
function initialAmounts(payment: Payment | null): Record<number, string> {
  const initial: Record<number, string> = {}
  for (const row of payment?.allocations ?? []) {
    initial[row.plan_id] = row.amount_exact ?? String(row.amount)
  }
  return initial
}

/**
 * 期 id → 金额（字符串，空串表示不分给这一期）。
 *
 * ⚠️ 用 `useState(初始化函数)` **惰性初值**，而不是 `useEffect` 里 setState：
 * 后者会让"打开弹窗"多跑一轮渲染，而且 oxlint 的
 * `react-hooks/set-state-in-effect` 会报 warning（生效的规则，不该绕）。
 * 父组件用 `key={payment?.id}` 让每次打开都是一次全新挂载，
 * 所以惰性初值天然就是"这次打开时的现状"，不会带上一次没提交的编辑。
 */
  const [amounts, setAmounts] = useState<Record<number, string>>(() => initialAmounts(payment))
  const [reason, setReason] = useState('')

  const parsed = useMemo(() => {
    try {
      return { total: Object.values(amounts).reduce((sum, value) => sum + (value.trim() ? paymentCents(value) : 0n), 0n), valid: true }
    } catch { return { total: 0n, valid: false } }
  }, [amounts])
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
      reasons.push('只有已确认的回款才谈得上分配；这笔还在待确认')
    }
    if (over) {
      reasons.push(
        `分配合计 ${formatMoney(total, currency)} 超过这笔回款的金额 ${formatMoney(received, currency)}`
        + '；多出来的部分没有来源，请先核对金额或减少分配',
      )
    }
    if (!reason.trim()) reasons.push('要填一句调整原因（它会写进审计，事后要能回答为什么这么分）')
    return reasons
  }, [canEdit, payment, over, total, received, currency, reason, parsed.valid])

  /**
   * 「按当前应收自动铺一遍」：按到期日从早到晚填各期空缺 ——
   * 与后端自动分配的规则一致（`payment.allocation` 的规则），
   * 这样人按一下就能得到"系统会给的分法"，再在上面微调。
   */
  const fillByDueDate = () => {
    const { amounts: next, unallocated: rest } = fillPaymentByDueDate(received, plans, payment?.allocations ?? [])
    setAmounts(next)
    if (rest > 0) {
      Toast.info(
        `按到期日铺完后还剩 ${formatMoney(rest, currency)} 没有对应缺口，`
        + '可能是多收了；这部分不会再分给任何一期',
      )
    }
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
      Toast.success('回款分配已调整（原归属与新归属都已留审计）')
      queryClient.invalidateQueries({ queryKey: ['order-payments'] })
      queryClient.invalidateQueries({ queryKey: ['order-receivables'] })
      queryClient.invalidateQueries({ queryKey: ['order-milestones'] })
      queryClient.invalidateQueries({ queryKey: ['order-finance'] })
      queryClient.invalidateQueries({ queryKey: ['an-receivables'] })
      queryClient.invalidateQueries({ queryKey: ['an-payments'] })
      onSaved()
      onClose()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <Modal
      title={`调整回款分配${payment ? `：${formatMoney(payment.received_amount_exact ?? payment.received_amount, currency)}` : ''}`}
      visible={visible}
      onCancel={onClose}
      width={640}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            theme="solid"
            type="primary"
            loading={saveMutation.isPending}
            disabled={blockedReasons.length > 0}
            onClick={() => saveMutation.mutate()}
          >
            保存分配
          </Button>
        </>
      }
    >
      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 12 }}>
        一笔回款可以分到多期。原来登记在「{payment?.plan_name ?? '（未挂期）'}」，
        下面填的是「这笔钱实际覆盖哪几期、各多少」。没填的期即不分配。
      </div>

      <div style={{ marginBottom: 8 }}>
        <FormLabel hint="按到期日从早到晚填各期空缺，与系统自动分配的规则一致">
          本单各期
        </FormLabel>
        <Button size="small" onClick={fillByDueDate} disabled={!canEdit}>
          按到期日自动铺一遍
        </Button>
      </div>

      <div style={{ maxHeight: 260, overflowY: 'auto', marginBottom: 12 }}>
        {plans.length === 0 ? (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>本单还没有应收计划</div>
        ) : (
          plans.filter(plan => plan.status !== 'cancelled').map((plan) => {
            const current = (payment?.allocations ?? []).find((row) => row.plan_id === plan.id)
            return (
              <div
                key={plan.id}
                style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}
              >
                <div style={{ flex: 1 }}>
                  <div>
                    {plan.plan_name}
                    {current?.source === 'manual' && (
                      <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>（人工定过）</span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    应收 {formatMoney(plan.amount_exact ?? plan.amount, plan.currency)}
                    {' · '}
                    已分 {formatMoney(plan.received_amount_exact ?? plan.received_amount, plan.currency)}
                    {plan.due_date ? ` · 到期 ${plan.due_date}` : ''}
                  </div>
                </div>
                <Input
                  style={{ width: 160 }}
                  disabled={!canEdit}
                  value={amounts[plan.id] ?? ''}
                  placeholder="不分配"
                  onChange={(value) => setAmounts(prev => ({ ...prev, [plan.id]: value }))}
                />
              </div>
            )
          })
        )}
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 12,
          padding: '8px 10px',
          background: over ? 'var(--crm-error-soft, #fef0f0)' : 'var(--crm-bg-2, #f7f8fa)',
          borderRadius: 4,
          marginBottom: 12,
        }}
      >
        <span>
          分配合计 <b>{formatMoney(total, currency)}</b> / 这笔回款{' '}
          {formatMoney(received, currency)}
        </span>
        {parsed.total < receivedCents && !over && (
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            还有 {formatMoney(diff, currency)} 没有分到任何一期（会挂「待分配」）
          </span>
        )}
        {over && (
          <span style={{ fontSize: 12, color: 'var(--crm-error)' }}>
            超出 {formatMoney(paymentMoney(parsed.total - receivedCents), currency)}
          </span>
        )}
      </div>

      <div>
        <FormLabel required hint="会写进审计的 after，事后要能回答为什么这么分">
          调整原因
        </FormLabel>
        <Select
          style={{ width: '100%', marginBottom: 6 }}
          placeholder="常见原因（也可以直接在下面写）"
          disabled={!canEdit}
          value={undefined}
          onChange={(value) => setReason(String(value))}
          optionList={[
            { value: '回款登记的期与实际到账期不一致，改到正确的期', label: '登记的期挂错了' },
            { value: '客户提前支付了后续期款项，按客户指定分配到对应期', label: '客户提前付款' },
            { value: '按到期日重新分配，使各期结清情况与客户对账单一致', label: '对齐客户对账单' },
          ]}
        />
        {/* 用 Semi 的 TextArea（与全站一致），不要手写 textarea ——
            我第一版手写了一个，还多留了一个隐藏的 InputNumber 占位，都是废件。 */}
        <TextArea
          rows={2}
          value={reason}
          disabled={!canEdit}
          placeholder="例如：客户实际付的是二期，登记时点成了一期"
          onChange={(value) => setReason(value)}
        />
      </div>

      {blockedReasons.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <BlockedHint reasons={blockedReasons} action="保存分配" />
        </div>
      )}
    </Modal>
  )
}
