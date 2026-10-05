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

/** 已知字段的中文列名；没映射的键直接用原词。 */
const KEY_LABEL: Record<string, string> = {
  action: '建议动作',
  reason: '理由',
  priority: '优先级',
  suggested_channel: '建议方式',
  sku_code: 'SKU',
  sku: 'SKU',
  name: '名称',
  specification: '规格',
  unit: '单位',
  moq: 'MOQ',
  quantity: '数量',
  order_times: '成交次数',
  last_unit_price: '最近成交价',
  quoted_price: '建议报价',
  minimum_price: '最低允许价',
  profit: '利润',
  profit_rate: '利润率',
  target_price: '目标价',
  suggested_price: '建议价',
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

/** 把一个字符串键值对象渲染成两列小表（用于 summary 这类汇总块）。 */
function KeyValueRows({ data }: { data: Record<string, unknown> }) {
  return (
    <div style={{ display: 'grid', gap: 4, fontSize: 13 }}>
      {Object.entries(data)
        .filter(([, value]) => typeof value !== 'object')
        .map(([key, value]) => (
          <div key={key} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--crm-text-3)' }}>{KEY_LABEL[key] ?? key}</span>
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
        title: KEY_LABEL[key] ?? key,
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
