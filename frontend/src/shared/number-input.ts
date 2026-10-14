/**
 * 表单里的"数字文本 → 提交值"解析。**全项目只此一处**（2026-10-14 收口）。
 *
 * ## 为什么必须共用一份
 *
 * 各处原来直接写 `Number(x || 0)` 或 `Number(x)`，于是有两类**静默丢输入**：
 *
 * 1. `Number('abc' || 0)` —— `'abc'` 是 truthy，`|| 0` 根本不起作用，
 *    结果是 `NaN`；而 `JSON.stringify({x: NaN})` 产出 `{"x":null}`，
 *    后端把 `null` 理解成"**没提供**这个字段"。
 *    实测（价格中心成本表单）：采购成本填 `abc` 点保存 → 界面回"成本已保存"，
 *    库里那项变成空的 → 核价按更低的成本算 → 毛利虚高、本该触发的低价审批不触发。
 * 2. `Number('' || 0)` —— 空串被静默转成 **0**。对"必填"的公斤单价，
 *    清空后保存会**建一条 0 元/kg 的费率**，所有走这条线路的运费算成 0。
 *
 * 正确做法是把三种情况分开（这个判据原来长在
 * `PriceCenterPage.parseOptionalNumber` 里，注释已经写明"各处直接写 Number(x)"
 * 的教训，但**其它文件又各写了一遍**，于是同一个坑在别处复现 ——
 * 所以这次把它抽成共享件，让"再写一遍"不再是最省事的做法）：
 *
 * - 空串 / 只有空白 → `null`（= 明确清空，合法操作）
 * - 能解析成有限数字 → 数字
 * - 其它（字母、`1abc`、`Infinity`…）→ **`NaN`**，调用方**必须**拦下并提示
 *
 * ⚠️ 调用方不要自己再写 `Number(...)`；用本文件的函数，并在 `NaN` 时
 * 抛错/提示（`firstInvalidNumberField` 帮你点名是哪个字段）。
 */

/** 把表单里的数字文本解析成提交值。空串 → `null`；坏输入 → `NaN`。 */
export function parseOptionalNumber(raw: unknown): number | null {
  const text = String(raw ?? '').trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) ? n : NaN
}

/**
 * 把表单里的数字文本解析成**必填**数字（空串也算坏输入 → `NaN`）。
 *
 * 用于"清空 = 静默变 0"会出事的字段（例如运费费率的公斤单价）——
 * 空串与字母都不接受，由调用方提示。
 */
export function parseRequiredNumber(raw: unknown): number {
  const text = String(raw ?? '').trim()
  if (text === '') return NaN
  const n = Number(text)
  return Number.isFinite(n) ? n : NaN
}

/** 把表单里的数量文本解析成提交值，并限制最多 3 位小数（与库列 `Numeric(16,3)` 对齐）。
 *
 * 不在前端先拦的话，`1.2349` 会被数据库静默舍成 `1.235`，用户以为上限是 1.2349，
 * 之后看到"区间重叠"也复现不出原因（审查 R04 的那个坑）。
 */
export function parseQuantity(raw: unknown): number | null {
  const n = parseOptionalNumber(raw)
  if (n === null || Number.isNaN(n)) return n
  const decimals = (String(raw).split('.')[1] ?? '').length
  return decimals > 3 ? NaN : n
}

/**
 * 必填数字：空串或坏输入都**抛错并点名**（不给"静默变 0"的机会）。
 *
 * 用于那些"清空 = 静默变 0 会出大事"的字段 —— 例如运费费率的公斤单价：
 * 清空后保存会建一条 **0 元/kg** 的费率，所有走这条线路的运费算成 0。
 */
export function requiredNumberOrThrow(
  raw: unknown,
  label?: string,
  hint = '必须填数字',
): number {
  const n = parseRequiredNumber(raw)
  if (Number.isNaN(n)) {
    throw new Error(`${label ?? '这一项'}${hint}`)
  }
  return n
}

/** 字段名 → 中文，用于"哪个字段填错了"的提示。缺省时回退字段名本身。 */
export const NUMBER_FIELD_LABEL: Record<string, string> = {
  min_qty: '数量下限',
  max_qty: '数量上限',
  standard_price: '标准价',
  guide_price: '指导价',
  minimum_price: '最低保护价',
  target_margin: '目标利润率',
  agreed_price: '约定价',
  purchase_cost: '采购成本',
  package_cost: '包装成本',
  production_cost: '生产成本',
  processing_cost: '加工成本',
  minimum_margin: '最低利润率',
  unit_price_per_kg: '公斤单价',
  min_charge: '最低收费',
  new_customer_target: '新客目标',
  sales_target: '销售额目标',
  repeat_customer_target: '复购目标',
  sort_no: '排序号',
  expected_amount: '预计金额',
  price_assumption: '价格假设',
}

/** 从"已解析的值"里挑出坏字段（`NaN`）。返回字段名列表，空 = 全 OK。 */
export function firstInvalidNumberField(
  values: Record<string, number | null>,
): string[] {
  return Object.entries(values)
    .filter(([, v]) => typeof v === 'number' && Number.isNaN(v))
    .map(([k]) => k)
}

/** 给一句人话的错误信息（点名第一个坏字段）。全 OK 时返回空串。 */
export function invalidNumberMessage(
  values: Record<string, number | null>,
  hint = '只能填数字',
): string {
  const bad = firstInvalidNumberField(values)
  if (!bad.length) return ''
  return `${NUMBER_FIELD_LABEL[bad[0]] ?? bad[0]}${hint}`
}
