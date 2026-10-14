/**
 * 报价"这一步还差什么"的**单一真源**（2026-10-13，主人：把阻塞原因收成一个待办区）。
 *
 * ## 为什么单独拎出来
 *
 * 前几轮攒下了不少零件：`BlockedHint` 共用件、`blocked-hint-reasons`、
 * 各处动作自己的 `can*` 判据、后端给的 `master_warnings` / `freight_unconfirmed_reason`。
 * 但它们**散在各页各处** —— 用户要自己一处处找"到底卡在哪、现在该点哪个"。
 *
 * 这个模块把"这一单还差什么"按阶段算成一份清单，页面上只渲染它。
 *
 * ## 两条纪律
 *
 * 1. **不许编理由**。每一项都必须对应页面/后端已有的真实判据，且在这里写明它来自哪。
 *    `ops/check_quote_todo_source.py` 会静态核对"这里列的门，页面上真的有"，
 *    以及"页面上的门，这里没有漏"——两侧任何一边漂移都会红。
 * 2. **能点的给入口**。只说明"差什么"而不给去处，等于把问题换个地方放着。
 *    每项带 `anchor`（页内定位）或 `action`（该点的按钮），没有去处的项在检查里单列。
 *
 * ## 什么算"待办"
 *
 * 只收**当前版本的推进**（能不能提交审批 → 能不能发客户 → 客户接受 → 转单）。
 * 历史版本"不能再改"这类**不是待办**（那是既成事实，页面上有 BlockedHint 说明），
 * 所以不收进来。
 */

/** 报价当前所处阶段。顺序即推进顺序，`blocked` 单独一档。 */
export type QuoteStage =
  | 'master'      // 明细/主数据还没准备好
  | 'cost'        // 费用（运费）还没确认
  | 'draft'       // 可以提交审批
  | 'approving'   // 审批中
  | 'rejected'    // 审批被拒，要改
  | 'approved'    // 待发送客户
  | 'sent'        // 等客户答复
  | 'accepted'    // 可转订单
  | 'declined'    // 客户拒绝（终态）
  | 'converted'   // 已转订单（终态）

export interface QuoteTodoInput {
  /** 版本详情接口附带的 `master_warnings`（后端按快照算） */
  masterWarnings: readonly string[] | null | undefined
  /** 版本详情接口附带的 `freight_unconfirmed_reason`；有值 = 运费没确认 */
  freightUnconfirmedReason: string | null | undefined
  approvalStatus: string | null | undefined
  sentAt: string | null | undefined
  acceptedAt: string | null | undefined
  declinedAt: string | null | undefined
  /** 这一版已经转成订单了没有（页面用 quote.status / order 反查，这里只收结论） */
  converted: boolean
}

export interface QuoteTodo {
  /** 稳定 key，前端渲染与检查脚本都按它对齐 */
  key: string
  stage: QuoteStage
  /** 一句话说清"差什么"（页面直接显示） */
  reason: string
  /** 这一项来自哪个真实判据（检查脚本核对用；页面不显示） */
  source: string
  /** 该去页面的哪里处理（锚点 id）；没有去处时为 null —— 检查脚本会点出来 */
  anchor: string | null
  /** 该点的按钮文案（与页面上真实存在的按钮一致）；不需要点按钮时 null */
  action: string | null
}

/** 阶段的人话名。页面顶部按它显示"现在到哪一步了"。 */
export const STAGE_LABEL: Record<QuoteStage, string> = {
  master: '准备明细',
  cost: '确认费用',
  draft: '待提交审批',
  approving: '审批中',
  rejected: '审批未通过',
  approved: '待发送客户',
  sent: '等客户答复',
  accepted: '待转订单',
  declined: '客户已拒绝',
  converted: '已转订单',
}

/** 阶段顺序（用于"第几步 / 共几步"与进度感）；终态不进进度条。 */
export const STAGE_ORDER: readonly QuoteStage[] = [
  'master',
  'cost',
  'draft',
  'approving',
  'approved',
  'sent',
  'accepted',
]

/**
 * 算"这一单现在卡在哪、下一步做什么"。
 *
 * 返回**第一项就是当前该做的事**（页面突出显示它），其余是后续阶段的提示。
 */
export function quoteTodos(input: QuoteTodoInput): QuoteTodo[] {
  const todos: QuoteTodo[] = []
  const master = (input.masterWarnings ?? []).filter((w) => Boolean(w && w.trim()))
  const approval = input.approvalStatus ?? 'not_submitted'

  // ① 主数据未确认：后端按"明细引用的那一版快照里有没有名称/规格/单位"算出来。
  //    草稿仍可建，但**正式发送会被拦**，所以它是"准备阶段"的第一道门。
  if (master.length) {
    todos.push({
      key: 'master_unconfirmed',
      stage: 'master',
      reason: `有 ${master.length} 条明细的主数据还没确认，正式发送前必须先处理：${master[0]}`,
      source: 'detail.master_warnings（后端 §8.14 快照判据）',
      anchor: 'quote-items',
      action: '已确认？重新核价接入',
    })
  }

  // ② 运费未确认：后端给的人话原因（前端不自己判，避免两套口径）。
  if (input.freightUnconfirmedReason) {
    todos.push({
      key: 'freight_unconfirmed',
      stage: 'cost',
      reason: input.freightUnconfirmedReason,
      source: 'detail.freight_unconfirmed_reason（后端运费闸）',
      anchor: 'quote-charges',
      action: '补填运费',
    })
  }

  // ③ 审批状态机（终态优先，命中即停）
  if (input.declinedAt) {
    return [
      ...todos,
      {
        key: 'declined',
        stage: 'declined',
        reason: '客户已拒绝这一版；要再谈请新建一版报价，或按失单流程处理商机。',
        source: 'version.declined_at',
        anchor: null,
        action: null,
      },
    ]
  }
  if (input.converted) {
    return [
      ...todos,
      {
        key: 'converted',
        stage: 'converted',
        reason: '这一版已经转成销售订单，后续在订单页作业。',
        source: 'quote.status / 订单反查',
        anchor: null,
        action: null,
      },
    ]
  }
  if (input.acceptedAt) {
    todos.push({
      key: 'convert_to_order',
      stage: 'accepted',
      reason: '客户已接受，可以转销售订单了。转单时能直接选收款方式（全款或分期）。',
      source: 'version.accepted_at + 转单前置（后端 ensure_current_version）',
      anchor: null,
      action: '转销售订单',
    })
    return todos
  }
  if (input.sentAt) {
    todos.push({
      key: 'await_customer',
      stage: 'sent',
      reason: '已发给客户，等客户答复。客户接受后才能转订单；被拒绝要新建一版。',
      source: 'version.sent_at',
      anchor: null,
      action: '客户接受',
    })
    return todos
  }
  if (approval === 'pending') {
    todos.push({
      key: 'approval_pending',
      stage: 'approving',
      reason: '这一版正在审批中。审批通过后才能发给客户；要改得先撤回审批。',
      source: 'version.approval_status = pending',
      anchor: null,
      action: '撤回审批',
    })
    return todos
  }
  if (approval === 'rejected') {
    todos.push({
      key: 'approval_rejected',
      stage: 'rejected',
      reason: '审批未通过，请按驳回意见调整价格后新建一版重新提交。',
      source: 'version.approval_status = rejected',
      anchor: null,
      action: '新建版本',
    })
    return todos
  }
  if (approval === 'approved') {
    todos.push({
      key: 'send_to_customer',
      stage: 'approved',
      reason: '审批已通过，可以发给客户了。发送后这一版就锁定，不能再改。',
      source: 'version.approval_status = approved',
      anchor: null,
      action: '标记已发送',
    })
    return todos
  }
  // not_submitted（或未知）：提交审批是下一步。前面若还有主数据/运费未处理，
  // 页面会把它们排在这个前面（数组顺序即优先级）。
  todos.push({
    key: 'submit_approval',
    stage: 'draft',
    reason:
      todos.length > 0
        ? '上面这些处理完就可以提交审批了。'
        : '这一版还没提交审批。提交后价格与明细会被锁定，审批通过才能发给客户。',
    source: 'version.approval_status = not_submitted',
    anchor: null,
    action: '提交审批',
  })
  return todos
}

/** 第一项 = 当前该做的事（页面突出显示）。空数组 = 没有待办（终态或数据未就绪）。 */
export function primaryTodo(todos: readonly QuoteTodo[]): QuoteTodo | null {
  return todos.length ? todos[0] : null
}

/** 当前阶段：取第一项的阶段；没有待办时返回 null。 */
export function currentStage(todos: readonly QuoteTodo[]): QuoteStage | null {
  return todos.length ? todos[0].stage : null
}

/** "第 N 步 / 共 M 步"，只在推进阶段给；终态返回 null。 */
export function stageProgress(stage: QuoteStage | null): { step: number; total: number } | null {
  if (!stage) return null
  const index = STAGE_ORDER.indexOf(stage)
  if (index < 0) return null
  return { step: index + 1, total: STAGE_ORDER.length }
}

/**
 * 主/次动作的划分（2026-10-13，报价页样板的**后半：按钮分层**）。
 *
 * 主人原话：「每个阶段突出一个主要动作……'新建版本、下载、历史操作'等放到次要位置」。
 *
 * ## 为什么放在这个文件里
 *
 * "现在该做什么"与"该突出哪个按钮"**必须是同一份判据** —— 分开写迟早会漂移成
 * "待办区说去发送、页面上最显眼的是新建版本"。所以主动作直接由阶段推出，
 * 页面只消费结论，不自己再判一遍（`ops/check_quote_todo_source.py` 会核对）。
 *
 * ## 界面文案与动作的对应
 *
 * - `label` 是**页面按钮上真实显示的文案**（检查脚本会拿它比对页面）；
 * - `handler` 是页面上的动作标识（页面据此把按钮接到自己的处理函数/弹窗上）；
 *   `handler: null` 表示这个阶段没有页面级主动作（例如明细还没准备好，
 *   该去处理的是"确认主数据"，而不是顶部某个按钮）。
 */
export interface QuoteActions {
  /** 页面上最显眼的那个按钮；null = 这一阶段的主动作不在顶部（见待办项的 anchor） */
  primary: { label: string; handler: string } | null
  /** 与当前阶段强相关、需要一直看得见的次要动作 */
  secondary: readonly { label: string; handler: string }[]
}

const ACTIONS_BY_STAGE: Record<QuoteStage, QuoteActions> = {
  master: { primary: null, secondary: [] },
  cost: { primary: null, secondary: [] },
  draft: { primary: { label: '提交审批', handler: 'submit' }, secondary: [] },
  approving: { primary: { label: '撤回审批', handler: 'withdraw' }, secondary: [] },
  rejected: { primary: { label: '新建版本', handler: 'newVersion' }, secondary: [] },
  approved: { primary: { label: '标记已发送', handler: 'markSent' }, secondary: [] },
  // 已发送之后还能再发一次（补发/重发），这是同阶段里的第二次动作
  sent: {
    primary: { label: '登记再次发送', handler: 'markSent' },
    secondary: [{ label: '客户接受', handler: 'accept' }],
  },
  accepted: { primary: { label: '转销售订单', handler: 'convert' }, secondary: [] },
  declined: { primary: { label: '新建版本', handler: 'newVersion' }, secondary: [] },
  converted: { primary: null, secondary: [] },
}

/** 取当前阶段的主/次动作。没有待办（终态或数据未就绪）时返回空。 */
export function quoteActions(stage: QuoteStage | null): QuoteActions {
  if (!stage) return { primary: null, secondary: [] }
  return ACTIONS_BY_STAGE[stage] ?? { primary: null, secondary: [] }
}

/**
 * "更多操作"里收起来的东西（与阶段无关，低频或与当前推进无关）。
 *
 * ⚠️ 收起来**不等于藏起来**：这些入口在折叠区里仍然可点，且理由照旧由
 * `BlockedHint` 说明（项目口径：能点就点得动，点不动就说清为什么）。
 */
export const SECONDARY_ACTIONS = [
  '新建版本',
  '下载 PDF',
  '一键刷新到最新适用价',
] as const

/**
 * 按比例拆分期金额 —— **与后端 `payment/service.split_amounts` 同一套算法**
 * （2026-10-13 第九批问题 ⑤）。
 *
 * ## 为什么不再用 `Math.round`
 *
 * 前端原来写的是 `Math.round(total * ratio * 100) / 100`：JavaScript 浮点，
 * 恰好半分时**向上**；后端是 Python `Decimal`，默认 `ROUND_HALF_EVEN`
 * （半分进偶数）。于是同一份输入两边结果相反：
 *
 *     0.05 元分两期 → 前端显示 0.03 / 0.02，后端落库 0.02 / 0.03
 *
 * 用户确认的金额与保存的金额不是一回事 —— 这是必须修的。
 *
 * ## 怎么保证一致
 *
 * 按 JSON 实际发送的十进制字符串转成 BigInt 系数与小数位，再做半分进偶数。
 * 不能先用浮点乘法：0.05 × 0.7 × 100 会变成 3.4999999999999996，
 * 把应当进位的 3.5 分算成 3 分。末期仍补差额，非正数期拒绝。
 */
export function splitAmountsLikeBackend(
  total: number,
  ratios: readonly number[],
): number[] | null {
  if (!ratios.length || !Number.isFinite(total) || total <= 0
    || ratios.some((ratio) => !Number.isFinite(ratio) || ratio <= 0)) return null

  // Number.toString 与 JSON 中的十进制数一致，对应后端 Decimal(str(value))。
  const decimalParts = (value: number): { coefficient: bigint; scale: number } => {
    const [mantissa, exponent = '0'] = value.toString().toLowerCase().split('e')
    const [integer, fraction = ''] = mantissa.split('.')
    return {
      coefficient: BigInt(integer + fraction),
      scale: fraction.length - Number(exponent),
    }
  }
  const toCents = (coefficient: bigint, scale: number): bigint => {
    const centScale = scale - 2
    if (centScale <= 0) return coefficient * (10n ** BigInt(-centScale))
    const divisor = 10n ** BigInt(centScale)
    const whole = coefficient / divisor
    const remainder = coefficient % divisor
    return whole + (remainder * 2n > divisor
      || (remainder * 2n === divisor && whole % 2n !== 0n) ? 1n : 0n)
  }
  const amount = decimalParts(total)
  const totalCents = toCents(amount.coefficient, amount.scale)
  const cents: bigint[] = []
  let allocated = 0n
  for (let index = 0; index < ratios.length; index += 1) {
    const ratio = decimalParts(ratios[index])
    const part = index === ratios.length - 1
      ? totalCents - allocated
      : toCents(amount.coefficient * ratio.coefficient, amount.scale + ratio.scale)
    cents.push(part)
    allocated += part
  }
  if (cents.some((item) => item <= 0n)) return null
  return cents.map((item) => Number(item) / 100)
}
