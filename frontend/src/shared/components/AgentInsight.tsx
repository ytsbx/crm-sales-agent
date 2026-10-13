import { Table } from '@douyinfe/semi-ui'

import type { AnalysisEnvelope } from '../api/agent'

/**
 * Agent 专用分析接口（03-API §37）结果的统一渲染。
 *
 * 这些接口的返回是「本地确定性计算 + 可选 AI 叙述」的信封：
 * commentary 是模型叙述（没配模型时为 null），insights / suggestions /
 * recommendations / warnings / items / summary 是结构化结果。
 * 这里按字段形态自动渲染，不逐接口写死——后端加字段不用改这里。
 */

/**
 * 后端分析字段的中文名。
 *
 * ⚠️ **没映射的键不许把英文原词印给用户**（2026-10-13 修，主人当面指出）：
 * 回款风险分析的 `summary` 里 `overdue_count / overdue_amount / open_amount /
 * upcoming_count` 四个键当时没有中文名，于是界面上直接出现
 * `overdue_count 0`、`open_amount ¥501` 这种"半句英文"——
 * 主人原话：「ai分析这一块怎么不讲人话呀，不要有英文」。
 *
 * 原来的兜底是「没映射的键直接用原词」，等于**新增字段必然漏给用户看**，
 * 而且不报错、不失败。现在兜底改成可见的「未命名字段」，并且由
 * `ops/check_agent_analysis_labels.py` 在 CI 里**提前**把它挡住
 * （那个脚本会枚举后端分析信封里所有会渲染的键，缺一个就红）。
 *
 * 加字段时的规矩：后端 `insights.py` 新增一个会渲染的键 → 这里必须同时加中文名，
 * 否则 CI 会拦下来。
 */
const KEY_LABEL: Record<string, string> = {
  // —— 建议 / 动作类 ——
  action: '建议动作',
  reason: '理由',
  priority: '优先级',
  suggested_channel: '建议方式',
  note: '说明',
  // —— 商品 / 明细类 ——
  sku_code: 'SKU 编码',
  sku: 'SKU',
  sku_id: 'SKU 编号',
  opportunity_item_id: '需求明细编号',
  name: '名称',
  specification: '规格',
  unit: '单位',
  moq: '起订量',
  quantity: '数量',
  amount: '金额',
  // —— 价格 / 利润类 ——
  standard_price: '标准价',
  last_unit_price: '最近成交价',
  quoted_price: '建议报价',
  suggested_price: '建议价',
  minimum_price: '最低允许价',
  protection_price: '公司保护价',
  target_price: '目标价',
  profit: '利润',
  profit_rate: '利润率',
  order_times: '成交次数',
  approval_required: '需要审批',
  // —— 回款风险 summary（主人看到的那四个）——
  overdue_count: '逾期条数',
  overdue_amount: '逾期金额',
  open_amount: '未收金额',
  upcoming_count: '待到期条数',
}

/** 优先级等枚举值的中文显示。 */
const VALUE_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  normal: '中',
  low: '低',
  urgent: '紧急',
}

const isMoneyKey = (key: string) =>
  /price|profit|amount|total|cost/.test(key) && !/rate|count|times/.test(key)

const fmtValue = (key: string, value: unknown): string => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') {
    if (isMoneyKey(key)) return `¥${value.toLocaleString('zh-CN')}`
    return String(value)
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (key === 'priority' && typeof value === 'string') return VALUE_LABEL[value] ?? value
  return String(value)
}

/**
 * 键的中文名。没有映射时**不印英文原文**（那正是主人抱怨的形态），
 * 给一个可见的占位并在控制台喊一声，方便验收时一眼看出漏了哪个键。
 * 正常路径上 CI 的 `check_agent_analysis_labels.py` 会先把它拦住。
 */
const labelOf = (key: string): string => {
  const mapped = KEY_LABEL[key]
  if (mapped) return mapped
  // eslint-disable-next-line no-console
  console.warn(`[AgentInsight] 字段「${key}」没有中文名，请在 KEY_LABEL 里补上`)
  return '未命名字段'
}

/** 把一个字符串键值对象渲染成两列小表（用于 summary 这类汇总块）。 */
function KeyValueRows({ data }: { data: Record<string, unknown> }) {
  return (
    <div style={{ display: 'grid', gap: 4, fontSize: 13 }}>
      {Object.entries(data)
        .filter(([, value]) => typeof value !== 'object')
        .map(([key, value]) => (
          <div key={key} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--crm-text-3)' }}>{labelOf(key)}</span>
            <span style={{ fontWeight: 500 }}>{fmtValue(key, value)}</span>
          </div>
        ))}
    </div>
  )
}

/** 把 list[dict] 渲染成小表格；list[string] 渲染成要点列表。 */
function ListBlock({ rows }: { rows: unknown[] }) {
  if (rows.length === 0) return null
  if (typeof rows[0] !== 'object' || rows[0] === null) {
    return (
      <div style={{ fontSize: 13, display: 'grid', gap: 4 }}>
        {rows.map((item, index) => (
          <div key={index}>· {String(item)}</div>
        ))}
      </div>
    )
  }
  const records: Record<string, unknown>[] = (rows as Record<string, unknown>[])
    .slice(0, 10)
    .map((row, index) => ({ ...row, _idx: index }))
  const keys = Object.keys(records[0]).filter(
    (key) =>
      key !== '_idx' &&
      records.every((row) => typeof row[key] !== 'object' || row[key] === null) &&
      key !== 'id',
  )
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={records}
      rowKey="_idx"
      columns={keys.map((key) => ({
        title: labelOf(key),
        dataIndex: key,
        align: 'center' as const,
        render: (value: unknown) => fmtValue(key, value),
      }))}
      empty="无"
    />
  )
}

const LIST_FIELDS = ['suggestions', 'recommendations', 'warnings', 'insights', 'items'] as const

export default function AgentInsight({
  envelope,
  empty = '还没有运行分析',
}: {
  envelope: AnalysisEnvelope | null
  empty?: string
}) {
  if (!envelope) {
    return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>{empty}</div>
  }
  const summary =
    envelope.summary && typeof envelope.summary === 'object'
      ? (envelope.summary as Record<string, unknown>)
      : null

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {typeof envelope.level_label === 'string' && (
        <div>
          <span className="chip chip-warning">{envelope.level_label}</span>
        </div>
      )}
      {envelope.commentary ? (
        <div style={{ fontSize: 13, lineHeight: 1.7 }}>{envelope.commentary}</div>
      ) : (
        <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>{envelope.commentary_note}</div>
      )}
      {summary && <KeyValueRows data={summary} />}
      {LIST_FIELDS.map((field) => {
        const rows = envelope[field]
        return Array.isArray(rows) && rows.length > 0 ? (
          <ListBlock key={field} rows={rows} />
        ) : null
      })}
    </div>
  )
}
