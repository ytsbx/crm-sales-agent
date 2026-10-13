import { SHARED_ACTION_LABELS } from './audit-action-labels.generated'
/**
 * 审计日志「说人话」的三层映射：**对象类型 → 动作 → 字段**。
 *
 * 主人 2026-10-12 的反馈（原话）：「审计日志也说一堆不是人话」。
 * 截图里那一页当时是这样：对象列印数据库表名（`sku_identity_source #12`）、
 * 动作列印代码名（`price_refresh`）、变更内容直接 `JSON.stringify` 整坨
 * （`{"quote_no":"Q2026…","status":"draft",…}`）——非技术同事完全不知道在看什么。
 *
 * ## 三层各管一段
 *
 * 1. **对象**（`AUDIT_TYPE_LABEL`）：66 种 → 中文名。后端写审计时用的是
 *    **逻辑名**（不是表名），这里一一对应。认不出的兜底成「其他记录」，
 *    **绝不把 `sku_identity_source` 这种内部名字印到屏幕上**。
 * 2. **动作**（`AUDIT_ACTION_LABEL`）：156 个 → 中文名。价格中心那页只有 20 个，
 *    这里是全站全集；认不出的退回原值（这一列窄，能接受），
 *    但 `ops/check_audit_labels.py` 会在 CI 上把"没登记的动作"报出来。
 * 3. **字段**：中文名的真源在**后端** `app/core/patch_schema.py` 的 `FIELD_LABELS`
 *    （项目里唯一一张字段中文名表）—— 2026-10-12 这一轮把它从 95 条扩到 402 条，
 *    覆盖审计快照里 88% 的非技术字段；剩下的正是下面那 4 个同名不同义的字段。
 *    前端**不抄第二份**：`AUDIT_FIELD_LABEL_BY_TYPE` 只补"同名不同义"那部分，
 *    其余走 `FIELD_LABELS`（`core/errors.py` 已经按同一张表把校验错误翻成人话，
 *    两处共用一张表，改中文名只改一个地方）。
 *
 * ## 为什么还要"按对象定名"这一层
 *
 * 有 4 个字段**同名不同义**：`name` / `status` / `title` / `source`。
 * 同一个 `name` 在客户上是「客户名称」、在产品上是「产品名称」、在联系人上是「姓名」。
 * 通用表里**故意不登记**它们（猜错就是把"客户名称"显示成"产品名称"，
 * 审查报告里记过同类事故：标签名超长时提示写成了「产品名称」）。
 * 所以这几个只能按"它属于哪一类对象"来定名 —— `AUDIT_FIELD_LABEL_BY_TYPE`。
 */
import { AUDIT_FIELD_LABEL, AUDIT_TYPE_FIELDS } from './audit-field-labels'

export { AUDIT_FIELD_LABEL, AUDIT_TYPE_FIELDS }

/** 对象类型（后端写审计时的逻辑名）→ 中文名。 */
export const AUDIT_TYPE_LABEL: Record<string, string> = {
  agent_action: 'AI 助手操作',
  agent_session: 'AI 助手会话',
  analytics_actuals: '实绩快照',
  analytics_basis: '业绩口径',
  approval: '审批单',
  approval_definition: '审批流配置',
  approval_rule: '审批规则',
  auth: '登录与账号',
  biz_doc: '单据文件',
  biz_doc_template: '单据模板',
  case: '案例',
  contact: '联系人',
  contract: '合同文档',
  contract_template: '合同模板',
  custom_inquiry: '定制需求',
  customer: '客户',
  customer_duplicate_case: '撞单裁定',
  customer_level: '客户等级',
  customer_price_rule: '客户专属价',
  department: '部门',
  dictionary: '数据字典',
  exchange_rate: '汇率',
  external_collect: '外部数据采集',
  external_mapping: '外部数据映射',
  external_source: '外部数据源',
  file: '文件',
  followup: '跟进记录',
  integration: '外部集成',
  integration_diff: '集成差异',
  lead: '线索',
  logistics_quote: '物流试算',
  logistics_rate: '运费费率',
  loss_reason: '失单原因',
  notification: '站内通知',
  numbering_rule: '编号规则',
  oa_instance: '钉钉审批',
  opportunity: '商机',
  opportunity_stage: '商机阶段',
  order: '订单',
  order_milestone: '跟单节点',
  payment: '回款',
  price_permission: '价格权限',
  price_rule: '价格规则',
  product: '产品',
  product_cost: '产品成本',
  product_insight: '新品洞察',
  public_pool_candidate: '公海回收候选',
  public_pool_rule: '公海规则',
  quote: '报价单',
  quote_charge: '报价附加费用',
  quote_item: '报价明细',
  quote_version: '报价版本',
  receivable_plan: '应收节点',
  reconciliation_run: 'ERP 对账',
  role: '角色',
  sales_target: '销售目标',
  sample: '打样单',
  sample_item: '打样明细',
  setting: '系统配置',
  settings: '系统配置',
  sku: 'SKU',
  sku_identity_source: 'SKU 权威来源',
  tag: '客户标签',
  task: '任务',
  task_rule: '任务规则',
  user: '用户',
}

/** 认不出的对象类型：不印内部名，说人话。 */
export const AUDIT_TYPE_FALLBACK = '其他记录'

export function auditTypeLabel(type: string | null | undefined): string {
  if (!type) return '-'
  return AUDIT_TYPE_LABEL[type] ?? AUDIT_TYPE_FALLBACK
}

/**
 * 动作 → 中文名（156 个）。
 *
 * 与价格中心那页的 `ACTION_LABEL` 是**同一套词**（那页是全集的子集），
 * 口径以这里为准；两页都认得的动作，叫法必须一致，`ops/check_audit_labels.py`
 * 会核对"后端写过的动作是否都有登记"。
 */
/**
 * 动作中文名 —— **读共用那一份**（与后端时间线同一个 JSON）。
 *
 * 这里原来自己维护了 156 条，而后端 `timeline/service.py` 另有一张 23 条的短表：
 * 两张表各自漂移，同一个动作在"日志"和"审计"两个页面显示不同词，而且时间线那张
 * 表里没有的动作会**原样印英文码**（主人 2026-10-13 截图：
 * 「客户：merge」「客户：open_customer_duplicate_cases」）。
 *
 * 现在两份合成 `backend/app/core/audit_action_labels.json`，两边都读它 ——
 * 加动作只改一处，`ops/check_audit_labels.py` 会盯着。
 */
export const AUDIT_ACTION_LABEL: Record<string, string> = SHARED_ACTION_LABELS


/**
 * 动作名的中文。**认不出就退回原值**而不是"其他动作" ——
 * 动作名本身短、且带信息（`erp_xxx` 一眼能看出是 ERP 那摊事），
 * 比笼统的"其他"更有用；而"没登记"这件事由 CI 检查兜住，不靠屏幕。
 */
export function auditActionLabel(action: string | null | undefined): string {
  if (!action) return '-'
  return AUDIT_ACTION_LABEL[action] ?? action
}

/**
 * 同名不同义的字段：**只能**按对象类型定名（通用表里故意不登记它们）。
 *
 * 只列真会撞的 4 个：`name` / `status` / `title` / `source`。
 * 其余字段在全站含义一致，走 `AUDIT_FIELD_LABEL`。
 */
export const AUDIT_FIELD_LABEL_BY_TYPE: Record<string, Record<string, string>> = {
  customer: { name: '客户名称', status: '客户状态', source: '客户来源' },
  contact: { name: '联系人姓名', title: '职务', source: '联系人来源' },
  lead: { name: '线索名称', status: '线索状态', source: '线索来源' },
  opportunity: { title: '商机名称', status: '商机状态', source: '商机来源' },
  opportunity_stage: { name: '阶段名称', status: '阶段状态' },
  quote: { status: '报价状态' },
  order: { status: '订单状态' },
  sample: { status: '打样状态' },
  sample_item: {},
  task: { title: '任务标题', status: '任务状态', source: '任务来源' },
  custom_inquiry: { title: '需求标题', status: '需求状态' },
  product: { name: '产品名称', status: '产品状态' },
  sku: { name: 'SKU 名称', status: 'SKU 状态' },
  product_insight: { title: '洞察标题', status: '洞察状态', source: '洞察来源' },
  case: { title: '案例标题', status: '案例状态' },
  contract: { title: '合同名称', status: '合同状态' },
  biz_doc: { title: '单据标题', status: '单据状态' },
  tag: { name: '标签名称', status: '标签状态' },
  role: { name: '角色名称', status: '角色状态' },
  department: { name: '部门名称', status: '部门状态' },
  product_cost: {},
  price_rule: { status: '价格规则状态' },
  customer_price_rule: { name: '专属价名称', status: '状态' },
  approval_definition: { name: '审批流名称', status: '状态' },
  task_rule: { name: '规则名称', status: '规则状态' },
  numbering_rule: { name: '编号规则名称', status: '规则状态' },
  public_pool_rule: { status: '规则状态' },
  logistics_rate: { status: '费率状态' },
  loss_reason: { name: '失单原因', status: '状态' },
  file: { name: '文件名' },
  user: { name: '姓名', status: '账号状态' },
  payment: { status: '回款状态' },
  receivable_plan: { status: '应收状态' },
  contract_template: { name: '模板名称', status: '模板状态' },
  biz_doc_template: { name: '模板名称', status: '模板状态' },
  dictionary: { name: '字典项名称', status: '状态' },
  integration: { name: '集成名称', status: '集成状态' },
  notification: { title: '通知标题', status: '通知状态' },
  pool_candidate: { status: '回收状态' },
  public_pool_candidate: { status: '回收状态' },
  sales_target: { status: '目标状态' },
  oa_instance: { status: '审批状态' },
  approval: { status: '审批状态' },
  followup: {},
  order_milestone: { status: '节点状态' },
  quote_item: {},
  quote_charge: {},
  quote_version: { status: '版本审批状态' },
  sku_identity_source: { name: '来源名称', status: '来源状态' },
}

/**
 * 字段名的中文。
 *
 * ⚠️ 这一层**只管"同名不同义"那 4 个字段**（`name`/`status`/`title`/`source`）——
 * 按对象类型定名。其余字段由后端那张唯一表翻（`FIELD_LABELS` → 校验提示与
 * 审计展示共用一张），前端不再维护第二份，避免"改一处漏一处"。
 *
 * 查法：**先按对象类型，再退回通用表**。通用表来自后端 `FIELD_LABELS` 的导出，
 * 所以"客户名称/产品名称"这类不会互相串。
 *
 * 返回 `null` 表示"这个名字真认不出"：调用方**不要**把英文列名印到屏幕上，
 * 而是跳过它、并提示"还有 N 处可在原始数据里看"。
 */
export function auditFieldLabel(
  type: string | null | undefined,
  field: string,
): string | null {
  // 先按对象类型（管住同名不同义的那几个），再退回通用表（后端那张唯一表的导出）
  const byType = type ? AUDIT_FIELD_LABEL_BY_TYPE[type] : undefined
  return byType?.[field] ?? AUDIT_FIELD_LABEL[field] ?? null
}


/** 金额类字段：显示时带 ¥，否则「20」看不出是钱还是件。 */
const MONEY_FIELDS = new Set([
  'amount',
  'total_amount',
  'subtotal_amount',
  'charge_amount',
  'discount_amount',
  'goods_amount',
  'logistics_amount',
  'other_charge_amount',
  'quoted_price',
  'unit_price',
  'purchase_cost',
  'production_cost',
  'package_cost',
  'processing_cost',
  'total_cost',
  'standard_price',
  'guide_price',
  'minimum_price',
  'agreed_price',
  'received_amount',
  'expected_amount',
  'sample_fee',
  'cost_snapshot',
  'package_cost_snapshot',
  'logistics_cost_snapshot',
  'standard_price_snapshot',
  'recommended_price_snapshot',
  'minimum_price_snapshot',
  'profit_snapshot',
  'tax_refund_snapshot',
  'profit_with_refund_snapshot',
  'current_version_amount',
])

/** 比率类字段：库里存小数（0.15 = 15%），直接显示 0.15 会被当成 0.15%。 */
const RATIO_FIELDS = new Set(['target_margin', 'profit_rate_snapshot'])

/** 布尔字段：显示「是 / 否」。 */
const BOOL_FIELDS = new Set([
  'is_primary',
  'is_win',
  'is_loss',
  'is_discount',
  'approval_required',
  'enabled',
  'supersede_parent',
  'last_contact_unknown',
])

/** 这些是**技术字段**：改了也不该出现在「变更内容」里（内部编号、时间戳、校验值…）。 */
export const AUDIT_TECH_FIELDS = new Set([
  'id',
  'created_at',
  'updated_at',
  'deleted_at',
  'created_by',
  'request_key',
  'request_hash',
  'object_key',
  'checksum',
  'content_sha256',
  'file_sha256',
  'review_request_key',
  'source_key',
  'parent_id',
  'root_id',
  'version_no',
  'revision',
  'renderer_version',
  'sort_no',
  'password_hash',
])

/** 单个值 → 人能看的字。空值说「空」，不显示 null；对象/数组不往屏幕上放。 */
export function auditFieldValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '空'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) {
    // 数组：说"几条"，别把整坨 JSON 倒出来（`[object Object]` 更不能上屏）
    return `${value.length} 项`
  }
  if (typeof value === 'object') {
    // 对象：认不出就返回空串，调用方会跳过这一条 —— 半截话不如不说
    return ''
  }
  if (typeof value === 'number') {
    if (MONEY_FIELDS.has(key)) return `¥${value.toLocaleString('zh-CN')}`
    if (RATIO_FIELDS.has(key)) return `${(value * 100).toFixed(1)}%`
    if (BOOL_FIELDS.has(key)) return value ? '是' : '否'
    return value.toLocaleString('zh-CN')
  }
  const text = String(value)
  // 英文枚举值 → 中文（状态类最常见）
  return ENUM_LABEL[text] ?? text
}

/** 英文枚举值 → 中文。快照里存的是 `approved` 这种值，直接上屏是机器话。 */
export const ENUM_LABEL: Record<string, string> = {
  approved: '已通过',
  rejected: '未通过',
  pending: '待处理',
  pending_approval: '待审批',
  not_submitted: '未提交',
  approval_rejected: '审批未通过',
  auto_passed: '免审通过',
  draft: '草稿',
  sent: '已发送',
  accepted: '已接受',
  declined: '客户拒绝',
  converted: '已转下游',
  open: '进行中',
  developing: '开发中',
  closed: '已关闭',
  cancelled: '已取消',
  active: '启用',
  disabled: '停用',
  paused: '暂停',
  done: '已完成',
  partial: '部分收款',
  paid: '已收齐',
  overdue: '已逾期',
  planned: '已计划',
  shipped: '已发货',
  signed: '已签收',
  discarded: '已作废',
  void: '已作废',
  high: '高',
  medium: '中',
  low: '低',
  urgent: '紧急',
  self: '本人',
  department: '本部门',
  department_and_sub: '本部门及下级',
  all: '全部',
  WEB: '网页操作',
  API: '接口调用',
  AGENT: 'AI 助手',
  INTEGRATION: '外部集成',
  SCHEDULER: '定时任务',
  manual: '手工录入',
  import: '批量导入',
  wecom: '企业微信',
  dingtalk: '钉钉',
  phone: '电话',
  email: '邮件',
  visit: '拜访',
  other: '其他',
}

/**
 * 把一条审计记录的"变更内容"翻成人话。
 *
 * 规则（与价格中心那页同一套口径，别各写一份）：
 *  ① 认得出动作名就优先说"这次做了什么"（`after_data.message` 是后端写好的整句时直接用）；
 *  ② 否则逐字段比对 `before_data` / `after_data`，只列**真变了**的字段；
 *  ③ 字段名先按对象类型查（管住 `name`/`status` 这种同名不同义的），再查通用表；
 *  ④ 技术字段一律不展示；对象/数组不往屏幕上放；
 *  ⑤ 只有一边有值的（`空 → 1`）说成"结果"，不写成"变更"。
 *
 * 返回 `null` 表示"没什么可说的"（调用方会退回"看原始数据"）。
 */
export function auditChangeSummary(row: {
  business_type?: string | null
  action?: string | null
  before_data?: unknown
  after_data?: unknown
}): string | null {
  const type = row.business_type ?? null
  const before = (row.before_data ?? {}) as Record<string, unknown>
  const after = (row.after_data ?? {}) as Record<string, unknown>

  // ① 后端已经把结果写成一句中文时，直接端出来（别拿字段名再译一遍）
  const message = typeof after.message === 'string' ? after.message.trim() : ''
  if (message) return message

  const isBlank = (value: unknown) =>
    value === null ||
    value === undefined ||
    value === '' ||
    (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0)

  const parts: string[] = []
  const keys = [...new Set([...Object.keys(after), ...Object.keys(before)])]
  for (const key of keys) {
    if (AUDIT_TECH_FIELDS.has(key)) continue
    const oldValue = before[key]
    const newValue = after[key]
    if (String(oldValue ?? '') === String(newValue ?? '')) continue
    const label = auditFieldLabel(type, key)
    // ④ 认不出中文名的字段**不硬凑**（显示英文列名等于没说人话）；
    //    "其他字段有变动"由调用方的提示兜住，不在这里编一个名字
    if (!label) continue
    const oldText = auditFieldValue(key, oldValue)
    const newText = auditFieldValue(key, newValue)
    if (oldText === '' || newText === '') continue
    if (isBlank(oldValue)) {
      // ⑤ 从无到有 = 这次动作算出来的结果，不是"变更"
      parts.push(`${label} ${newText}`)
    } else if (isBlank(newValue)) {
      parts.push(`${label} 清空（原 ${oldText}）`)
    } else {
      parts.push(`${label}：${oldText} → ${newText}`)
    }
    if (parts.length >= 6) {
      parts.push('…')
      break
    }
  }
  return parts.length ? parts.join('；') : null
}

/**
 * 这条记录有多少字段**没被翻出来**（界面据此提示"还有 N 处技术细节，可展开原始数据"）。
 *
 * 审计页是合规取证用的（仅管理员可见），所以不能因为"翻不出中文"就把信息藏了 ——
 * 摘要给人看，原始 JSON 留在可展开的地方。
 */
export function auditHiddenFieldCount(row: {
  business_type?: string | null
  before_data?: unknown
  after_data?: unknown
}): number {
  const type = row.business_type ?? null
  const before = (row.before_data ?? {}) as Record<string, unknown>
  const after = (row.after_data ?? {}) as Record<string, unknown>
  let hidden = 0
  for (const key of new Set([...Object.keys(after), ...Object.keys(before)])) {
    if (AUDIT_TECH_FIELDS.has(key)) continue
    if (String(before[key] ?? '') === String(after[key] ?? '')) continue
    if (!auditFieldLabel(type, key)) hidden += 1
  }
  return hidden
}
