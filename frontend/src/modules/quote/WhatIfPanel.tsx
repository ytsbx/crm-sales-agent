import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Input, Slider, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  compareQuoteVersions,
  type QuoteItemRow,
  type VersionComparisonRow,
} from '../../shared/api/quote'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'

/**
 * 报价 What-if（UI 设计稿 `ai_sales_agent` 右栏「What-if 边际测算」+ 正文
 * 「多维度对标策略推演」A/B/C 方案对比表）。
 *
 * 两块：
 *  1. 版本对比表——V1/V2/V3 的报价、成本、毛利、毛利率并排，并列出每一版改了什么；
 *  2. 边际测算——拖动价格滑杆，按**本版快照成本**实时算利润额与利润率，
 *     同时显示授权底价 / 盈亏平衡价 / 建议价三条基准线。
 *
 * 口径说明：拖动时的成本用本版明细的加权平均单件成本
 * （`cost_snapshot` 商品成本 + `logistics_cost_snapshot` 单件运费，与核价的
 * base_cost 及明细里的 profit_snapshot 同口径）；数量按"调整比例同比缩放"估算；
 * 授权底价与保护价来自核价接口的实时结果。
 */

const APPROVAL_TONE: Record<string, TagTone> = {
  not_submitted: 'grey',
  pending: 'orange',
  approved: 'blue',
  rejected: 'red',
}

const APPROVAL_LABEL: Record<string, string> = {
  not_submitted: '未提交',
  pending: '审批中',
  approved: '已通过',
  rejected: '审批未通过',
}

const DIFF_LABEL: Record<string, string> = {
  added: '新增 SKU',
  removed: '移除 SKU',
  quantity: '数量',
  quoted_price: '单价',
  charge_amount: '附加费用',
  discount_amount: '折扣',
}

const money = (value?: number | null) =>
  value === null || value === undefined ? '-' : `¥${value.toFixed(2)}`
const percent = (value?: number | null) =>
  value === null || value === undefined ? '-' : `${(value * 100).toFixed(2)}%`

interface Props {
  quoteId: number
  versionId: number
  items: QuoteItemRow[]
}

export default function WhatIfPanel({ quoteId, versionId, items }: Props) {
  const comparisonQuery = useQuery({
    queryKey: ['quote-version-comparison', quoteId],
    queryFn: () => compareQuoteVersions(quoteId),
    enabled: Number.isFinite(quoteId),
  })

  const versions = comparisonQuery.data?.versions ?? []
  const currentVersion = versions.find((row) => row.version_id === versionId)

  const [targetPrice, setTargetPrice] = useState<number | null>(null)
  const [scenarios, setScenarios] = useState<Array<number | null>>([null, null, null])

  // 本版数量与成本基线（拖动滑杆时用它做实时估算）
  //
  // 成本口径坑：`cost_snapshot` 只是**商品成本**，单件总成本还要加
  // `logistics_cost_snapshot`（这正是 build_item_snapshot 里算 profit_snapshot 的口径，
  // 也是 pricing 的 base_cost = goods_cost + logistics）。少加运费，
  // 汇总毛利就会和明细表里逐条显示的利润对不上。这里优先用接口给的单件口径。
  const baseline = useMemo(() => {
    const quantity = items.reduce((sum, item) => sum + item.quantity, 0)
    const amountTotal = items.reduce((sum, item) => sum + item.quantity * item.quoted_price, 0)
    const costTotal = items.reduce(
      (sum, item) => sum + (item.cost_snapshot + item.logistics_cost_snapshot) * item.quantity,
      0,
    )
    const unitCost =
      currentVersion?.unit_cost ?? (quantity > 0 ? costTotal / quantity : 0)
    const unitPrice =
      currentVersion?.unit_price ?? (quantity > 0 ? amountTotal / quantity : 0)
    return { quantity, unitCost, unitPrice, amountTotal }
  }, [items, currentVersion])

  const basePrice = targetPrice ?? Number(baseline.unitPrice.toFixed(2))
  const ratio = baseline.unitPrice > 0 ? basePrice / baseline.unitPrice : 0
  const estimateQuantity = baseline.quantity * ratio

  const projected = useMemo(() => {
    const unitProfit = basePrice - baseline.unitCost
    const profitTotal = unitProfit * estimateQuantity
    const amountTotal = basePrice * estimateQuantity
    return {
      unitProfit,
      profitTotal,
      amountTotal,
      margin: amountTotal > 0 ? profitTotal / amountTotal : 0,
    }
  }, [basePrice, baseline.unitCost, estimateQuantity])

  // 基准线全部取**本版快照的整版加权值**：单 SKU 的建议价/最低价和整版均价
  // 不同量级，直接比较会得出"高出 116 元"这种看不懂的结论。
  const breakEven = baseline.unitCost
  const authorizedFloor = currentVersion?.unit_floor ?? null
  const standardPrice = currentVersion?.unit_standard ?? null
  const recommended = currentVersion?.unit_recommended ?? null

  const belowAuthorized = authorizedFloor !== null && basePrice < authorizedFloor

  const versionColumns = [
    {
      title: '方案',
      dataIndex: 'version_no',
      // 这一列要同时放「V3」和「当前」标记。原来写 90px：实测列里内容 64px、
      // 内边距各 12px，`当前` 标签右边缘**只剩 18px 就到列边界**，看着像被挤出去
      // （截图反馈：「v3 的位置被当前挤出去了」）。130px 之后两行都留得住。
      //
      // 另外用 `flex + gap` 而不是 `marginLeft`：间距由布局给，不靠手写魔法数字，
      // 标签换字号/改文案时不会又贴上去。`nowrap` 保证它永远不折到第二行。
      width: 130,
      render: (value: number, record: VersionComparisonRow) => {
        const isCurrent = record.version_id === versionId
        return (
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              whiteSpace: 'nowrap',
            }}
          >
            <span style={{ fontWeight: isCurrent ? 700 : 500 }}>V{value}</span>
            {isCurrent && (
              // `type="light"` 比默认实心填充轻一档：这里只是"标一下哪个是当前版"，
              // 不需要抢过右边「状态」列那个真正的状态标签。
              <Tag color="blue" size="small" type="light">
                当前
              </Tag>
            )}
          </span>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'approval_status',
      width: 96,
      render: (value: string, record: VersionComparisonRow) => (
        <Tag color={APPROVAL_TONE[value] ?? 'grey'} size="small">
          {APPROVAL_LABEL[value] ?? value}
          {record.approval_required ? '·含超权限' : ''}
        </Tag>
      ),
    },
    { title: '需求条数', dataIndex: 'item_count', width: 90, align: 'right' as const },
    {
      title: '数量',
      dataIndex: 'quantity',
      width: 90,
      align: 'right' as const,
      render: (value: number) => value.toLocaleString('zh-CN'),
    },
    {
      title: '报价金额',
      dataIndex: 'amount_total',
      width: 120,
      align: 'right' as const,
      render: (value: number) => money(value),
    },
    {
      title: '成本',
      dataIndex: 'cost_total',
      width: 110,
      align: 'right' as const,
      render: (value: number) => money(value),
    },
    {
      title: '毛利',
      dataIndex: 'profit_total',
      width: 110,
      align: 'right' as const,
      render: (value: number) => (
        <span style={{ color: value < 0 ? 'var(--crm-error)' : 'var(--crm-text)' }}>
          {money(value)}
        </span>
      ),
    },
    {
      title: '毛利率',
      dataIndex: 'margin',
      width: 100,
      align: 'right' as const,
      render: (value: number) => (
        <span style={{ color: value < 0 ? 'var(--crm-error)' : 'var(--crm-success)', fontWeight: 600 }}>
          {percent(value)}
        </span>
      ),
    },
    {
      title: '均价',
      dataIndex: 'avg_price',
      width: 90,
      align: 'right' as const,
      render: (value: number | null) => money(value),
    },
  ]

  const startPrice = Number(baseline.unitPrice.toFixed(2))
  const minPrice = Math.max(0, Math.round(Math.min(startPrice * 0.7, breakEven * 0.8) * 100) / 100)
  const maxPrice = Math.round(Math.max(startPrice * 1.15, (recommended ?? startPrice) * 1.1) * 100) / 100

  return (
    <>
      <SectionCard style={{ marginBottom: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600 }}>版本与方案对比（What-if）</div>
          <div style={{ flex: 1 }} />
          <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            旧版本不可覆盖，改价请新建版本
          </span>
        </div>

        <Table<VersionComparisonRow>
          columns={versionColumns}
          dataSource={versions}
          rowKey="version_id"
          pagination={false}
          size="small"
          loading={comparisonQuery.isLoading}
          empty="还没有版本"
          scroll={{ x: 1000 }}
        />

        {(comparisonQuery.data?.diffs ?? []).length > 0 && (
          <div style={{ marginTop: 14, display: 'grid', gap: 10 }}>
            {(comparisonQuery.data?.diffs ?? []).map((diff) => (
              <div
                key={`${diff.from_version_no}-${diff.to_version_no}`}
                style={{
                  padding: 10,
                  borderRadius: 'var(--crm-radius-sm)',
                  background: 'var(--crm-surface-low)',
                  fontSize: 12.5,
                }}
              >
                <div style={{ marginBottom: 6 }}>
                  <span style={{ fontWeight: 600 }}>
                    V{diff.from_version_no} → V{diff.to_version_no}
                  </span>
                  <span style={{ marginLeft: 10, color: 'var(--crm-text-3)' }}>
                    金额 {diff.amount_delta >= 0 ? '+' : ''}
                    {diff.amount_delta.toFixed(2)}，毛利 {diff.profit_delta >= 0 ? '+' : ''}
                    {diff.profit_delta.toFixed(2)}，毛利率 {diff.margin_delta >= 0 ? '+' : ''}
                    {(diff.margin_delta * 100).toFixed(2)}%
                  </span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {diff.changes.length === 0 && (
                    <span style={{ color: 'var(--crm-text-3)' }}>明细与费用没有变化</span>
                  )}
                  {diff.changes.map((change, index) => (
                    <span
                      key={`${change.field}-${change.sku_id ?? 'x'}-${index}`}
                      className="chip"
                      style={{ fontSize: 12 }}
                    >
                      {DIFF_LABEL[change.field] ?? change.field}
                      {change.sku_name ? `·${change.sku_name}` : ''}：
                      {change.before ?? '—'} → {change.after ?? '—'}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard style={{ marginBottom: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600 }}>边际测算（What-if）</div>
          <div style={{ flex: 1 }} />
          {targetPrice !== null && (
            <Button size="small" theme="borderless" onClick={() => setTargetPrice(null)}>
              重置到当前价
            </Button>
          )}
        </div>

        {items.length === 0 ? (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>本版还没有明细，无法测算</div>
        ) : (
          // 比例保持 1.4:1；最小宽度放开，避免列被内容固有宽度顶破
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)', gap: 20 }}>
            <div>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 6 }}>
                成交均价（拖动调整整版价格，同比缩放数量）
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <Slider
                    min={minPrice}
                    max={maxPrice}
                    step={0.01}
                    value={basePrice}
                    onChange={(value) => {
                      const next = Array.isArray(value) ? value[0] : value
                      if (typeof next === 'number') setTargetPrice(next)
                    }}
                    tipFormatter={(value) => `¥${Number(value).toFixed(2)}`}
                  />
                </div>
                <Input
                  style={{ width: 110 }}
                  value={String(basePrice)}
                  onChange={(value) => {
                    const parsed = Number(value)
                    setTargetPrice(Number.isFinite(parsed) ? parsed : null)
                  }}
                />
              </div>

              <div style={{ display: 'flex', gap: 20, marginTop: 12 }}>
                <div>
                  <div className="kpi-label">预计成交金额</div>
                  <div className="kpi-value" style={{ fontSize: 20 }}>
                    {money(projected.amountTotal)}
                  </div>
                </div>
                <div>
                  <div className="kpi-label">预计利润额</div>
                  <div
                    className="kpi-value"
                    style={{ fontSize: 20, color: projected.profitTotal < 0 ? 'var(--crm-error)' : undefined }}
                  >
                    {money(projected.profitTotal)}
                  </div>
                </div>
                <div>
                  <div className="kpi-label">预计利润率</div>
                  <div
                    className="kpi-value"
                    style={{ fontSize: 20, color: projected.margin < 0 ? 'var(--crm-error)' : 'var(--crm-success)' }}
                  >
                    {percent(projected.margin)}
                  </div>
                </div>
              </div>

              <div style={{ display: 'grid', gap: 6, marginTop: 14, fontSize: 12.5 }}>
                {(
                  [
                    ['本版单件总成本（含运费）', breakEven],
                    ['授权底价（整版加权）', authorizedFloor],
                    ['标准价（整版加权）', standardPrice],
                    ['建议价（整版加权）', recommended],
                  ] as Array<[string, number | null]>
                ).map(([label, value]) => {
                  const reached = value !== null && basePrice >= value
                  return (
                    <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ color: 'var(--crm-text-3)', minWidth: 168 }}>{label}</span>
                      <span style={{ minWidth: 90 }}>{money(value)}</span>
                      <Tag color={reached ? 'green' : 'red'} size="small">
                        {reached ? '已满足' : '未达到'}
                      </Tag>
                      <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                        {value === null
                          ? '本版快照没有这一项'
                          : basePrice >= value
                            ? `高出 ${(basePrice - value).toFixed(2)}`
                            : `还差 ${(value - basePrice).toFixed(2)}`}
                      </span>
                    </div>
                  )
                })}
              </div>

              {belowAuthorized || projected.profitTotal < 0 ? (
                <div
                  style={{
                    marginTop: 12,
                    padding: 10,
                    borderRadius: 'var(--crm-radius-sm)',
                    background: 'var(--crm-error-soft)',
                    color: 'var(--crm-error)',
                    fontSize: 12.5,
                  }}
                >
                  红线提醒：当前均价 {money(basePrice)}
                  {projected.profitTotal < 0 ? ' 已经亏损' : ''}
                  {belowAuthorized ? ' 已低于你的授权底价' : ''}
                  ，提交后会转主管审批，不要承诺客户可以特批。
                </div>
              ) : (
                <div style={{ marginTop: 12, color: 'var(--crm-text-3)', fontSize: 12.5 }}>
                  当前均价未触发红线，提交审批会直接通过（以你的价格权限为准）。
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                {(['A', 'B', 'C'] as const).map((label, index) => (
                  <Button
                    key={label}
                    size="small"
                    onClick={() => {
                      const next = [...scenarios]
                      next[index] = Number(basePrice.toFixed(2))
                      setScenarios(next)
                    }}
                  >
                    记为方案 {label}
                  </Button>
                ))}
              </div>
            </div>

            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>多维度方案对比</div>
              <Table
                columns={[
                  { title: '方案', dataIndex: 'label', width: 56 },
                  {
                    title: '成交均价',
                    dataIndex: 'price',
                    render: (value: number | null) => money(value),
                  },
                  {
                    title: '订单金额',
                    dataIndex: 'amount',
                    render: (value: number) => money(value),
                  },
                  {
                    title: '利润额',
                    dataIndex: 'profit',
                    render: (value: number) => (
                      <span style={{ color: value < 0 ? 'var(--crm-error)' : undefined }}>
                        {money(value)}
                      </span>
                    ),
                  },
                  {
                    title: '利润率',
                    dataIndex: 'margin',
                    render: (value: number) => (
                      <span style={{ color: value < 0 ? 'var(--crm-error)' : 'var(--crm-success)' }}>
                        {percent(value)}
                      </span>
                    ),
                  },
                ]}
                dataSource={scenarios.map((price, index) => {
                  const effective = price ?? 0
                  const quantity = baseline.quantity * (baseline.unitPrice > 0 ? effective / baseline.unitPrice : 0)
                  const amount = effective * quantity
                  const profit = (effective - baseline.unitCost) * quantity
                  return {
                    label: ['A', 'B', 'C'][index],
                    price,
                    amount,
                    profit,
                    margin: amount > 0 ? profit / amount : 0,
                  }
                })}
                rowKey="label"
                pagination={false}
                size="small"
                empty=""
              />
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 8 }}>
                把滑杆调到想试的价格后点「记为方案」，三个方案并排比较；未记录的方案显示为
                -。结果仅供内部测算，不要直接发给客户。
              </div>
              <Button
                size="small"
                style={{ marginTop: 8 }}
                onClick={() => {
                  setScenarios([null, null, null])
                  Toast.info('已清空方案')
                }}
              >
                清空方案
              </Button>
            </div>
          </div>
        )}
      </SectionCard>
    </>
  )
}
