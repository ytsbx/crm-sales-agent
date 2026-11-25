/** Exact decimal input for financial edits; never round excess decimal places silently. */
export function paymentCents(value: number | string): bigint {
  const text = String(value).trim()
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error('金额必须是非负数，最多两位小数')
  const [whole, fraction = ''] = text.split('.')
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
}
export function paymentMoney(cents: bigint): string {
  return `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`
}

/** Rebuild only the edited receipt. Other receipts' allocations remain occupied. */
export function fillPaymentByDueDate(
  received: number | string,
  plans: { id: number; amount: number; amount_exact?: string; received_amount: number; received_amount_exact?: string; due_date?: string | null; status: string }[],
  current: { plan_id: number; amount: number; amount_exact?: string }[],
): { amounts: Record<number, string>; unallocated: number } {
  const money = (value: bigint) => `${value / 100n}.${String(value % 100n).padStart(2, '0')}`
  const own = new Map(current.map(row => [row.plan_id, paymentCents(row.amount_exact ?? row.amount)]))
  let rest = paymentCents(received)
  const amounts: Record<number, string> = {}
  const ordered = plans.filter(p => p.status !== 'cancelled').slice().sort((a, b) => {
    const da = a.due_date ?? '', db = b.due_date ?? ''
    return da === db ? a.id - b.id : da < db ? -1 : 1
  })
  for (const plan of ordered) {
    if (rest <= 0n) break
    const other = paymentCents(plan.received_amount_exact ?? plan.received_amount) - (own.get(plan.id) ?? 0n)
    const gap = paymentCents(plan.amount_exact ?? plan.amount) - (other > 0n ? other : 0n)
    const take = gap > rest ? rest : gap
    if (take > 0n) {
      amounts[plan.id] = money(take)
      rest -= take
    }
  }
  return { amounts, unallocated: Number(money(rest)) }
}
