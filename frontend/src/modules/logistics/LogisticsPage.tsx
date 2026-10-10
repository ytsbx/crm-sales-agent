import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Banner, Button, Input, Select, Table, Tabs, Tag } from '@douyinfe/semi-ui'

import {
  calculateLogistics,
  listLogisticsQuotes,
  listLogisticsRoutes,
  listSkusForPricing,
  type LogisticsOption,
  type LogisticsRouteRow,
} from '../../shared/api/pricing'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { optionMatcher } from '../../shared/components/optionMatch'

/**
 * 物流试算（PRD §14 / 03-API §19）。
 *
 * 三个页签：试算 / 试算历史 / 线路速查。
 * 费率的新增维护留在「价格中心 › 运费费率」，那里原本就是费率的归属地，
 * 不在这里重复做一遍。
 */

function SummaryRow({ label, value, tone }: { label: string; value: string; tone?: 'primary' | 'muted' }) {
  const color =
    tone === 'primary' ? 'var(--crm-primary)' : tone === 'muted' ? 'var(--crm-text-3)' : 'var(--crm-text)'
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '10px 0',
        borderBottom: '1px solid var(--crm-surface-high)',
      }}
    >
      <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  )
}

function money(value?: number | null) {
  return value === null || value === undefined ? '-' : `¥${value.toFixed(2)}`
}

export default function LogisticsPage() {
  const [activeKey, setActiveKey] = useState('calc')

  // 支持深链预填：/logistics?sku_id=1&quantity=3000&destination=华东&shipping_method=陆运
  // 与核价页保持同一套参数名，商机/报价页以后可以照抄同一段跳转逻辑。
  const [searchParams] = useSearchParams()
  const paramNumber = (key: string) => {
    const raw = searchParams.get(key)
    return raw && Number.isFinite(Number(raw)) ? Number(raw) : undefined
  }

  const [skuId, setSkuId] = useState<number | undefined>(paramNumber('sku_id'))
  const [quantity, setQuantity] = useState(String(paramNumber('quantity') ?? 1000))
  const [origin, setOrigin] = useState(searchParams.get('origin') ?? '')
  const [destination, setDestination] = useState(searchParams.get('destination') ?? '')
  const [shippingMethod, setShippingMethod] = useState<string | undefined>(
    searchParams.get('shipping_method') ?? undefined,
  )
  const [packageType, setPackageType] = useState('')
  // 手工覆盖：分包装或特殊装载时，SKU 上的重量体积不准
  const [weightOverride, setWeightOverride] = useState('')
  const [volumeOverride, setVolumeOverride] = useState('')

  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  const routesQuery = useQuery({ queryKey: ['logistics-routes'], queryFn: listLogisticsRoutes })
  const historyQuery = useQuery({
    queryKey: ['logistics-quotes'],
    queryFn: () => listLogisticsQuotes({ page: 1, page_size: 50 }),
    enabled: activeKey === 'history',
  })

  const quantityNumber = Number(quantity)
  const canCalc = Boolean(skuId) && Number.isFinite(quantityNumber) && quantityNumber > 0

  const payload = useMemo(
    () => ({
      sku_id: skuId,
      quantity: Number.isFinite(quantityNumber) && quantityNumber > 0 ? quantityNumber : 1,
      origin: origin.trim() || null,
      destination: destination.trim() || null,
      shipping_method: shippingMethod ?? null,
      package_type: packageType.trim() || null,
      weight_override: weightOverride.trim() ? Number(weightOverride) : null,
      volume_override: volumeOverride.trim() ? Number(volumeOverride) : null,
    }),
    [skuId, quantityNumber, origin, destination, shippingMethod, packageType, weightOverride, volumeOverride],
  )

  const resultQuery = useQuery({
    queryKey: ['logistics-calc', payload],
    queryFn: () => calculateLogistics(payload),
    enabled: canCalc,
  })
  const result = resultQuery.data

  // 运输方式选项来自费率表，避免出现"选了却没有方案"的空选项
  const methodOptions = useMemo(() => {
    const set = new Set<string>()
    for (const route of routesQuery.data ?? []) set.add(route.shipping_method)
    return [...set].map((method) => ({ value: method, label: method }))
  }, [routesQuery.data])

  const optionColumns = [
    {
      title: '承运商',
      dataIndex: 'provider',
      render: (value: string, record: LogisticsOption) => (
        <div>
          <div style={{ fontWeight: 600 }}>{value}</div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            {record.shipping_method}
            {record.destination_region ? ` · ${record.destination_region}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '费用',
      dataIndex: 'amount',
      width: 130,
      render: (value: number) => <span style={{ fontWeight: 600 }}>{money(value)}</span>,
    },
    {
      title: '计价口径',
      dataIndex: 'pricing_basis',
      width: 150,
      render: (value: string, record: LogisticsOption) => (
        <div style={{ fontSize: 12 }}>
          <Tag size="small" color={value === '体积' ? 'orange' : 'blue'}>
            {value}计价
          </Tag>
          <div style={{ color: 'var(--crm-text-3)', marginTop: 4 }}>
            重量 {money(record.by_weight_amount)} / 体积 {money(record.by_volume_amount)}
          </div>
        </div>
      ),
    },
    {
      title: '时效',
      dataIndex: 'eta_text',
      width: 110,
      render: (value: string | null) => value ?? '-',
    },
    {
      title: '最低收费',
      dataIndex: 'min_charge',
      width: 110,
      render: (value: number | null, record: LogisticsOption) => (
        <span style={{ fontSize: 12 }}>
          {money(value)}
          {!record.above_minimum && (
            <Tag size="small" color="grey" style={{ marginLeft: 4 }}>
              已触发
            </Tag>
          )}
        </span>
      ),
    },
  ]

  const historyColumns = [
    { title: '承运商', dataIndex: 'provider', render: (value: string | null) => value ?? '-' },
    { title: '运输方式', dataIndex: 'shipping_method', width: 100 },
    {
      title: '起运 → 目的',
      width: 180,
      render: (_: unknown, record: { origin?: string | null; destination?: string | null }) =>
        `${record.origin || '-'} → ${record.destination || '-'}`,
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      width: 90,
      render: (value: number | null) => (value === null ? '-' : value.toLocaleString('zh-CN')),
    },
    {
      title: '计费重 (kg)',
      dataIndex: 'chargeable_weight',
      width: 120,
      render: (value: number) => value.toLocaleString('zh-CN'),
    },
    { title: '运费', dataIndex: 'amount', width: 110, render: (value: number) => money(value) },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string | null) => (value ? new Date(value).toLocaleString('zh-CN') : '-'),
    },
  ]

  const routeColumns = [
    {
      title: '起运地',
      dataIndex: 'origin',
      render: (value: string | null) => value ?? '不限',
    },
    {
      title: '目的地',
      dataIndex: 'destination',
      render: (value: string | null) => value ?? '全国',
    },
    { title: '运输方式', dataIndex: 'shipping_method' },
    {
      title: '承运商',
      dataIndex: 'providers',
      render: (value: string[]) => value.join('、'),
    },
    {
      title: '时效',
      width: 120,
      render: (_: unknown, record: LogisticsRouteRow) => {
        if (record.eta_days_min == null) return '-'
        if (record.eta_days_max == null || record.eta_days_max === record.eta_days_min) {
          return `约 ${record.eta_days_min} 天`
        }
        return `${record.eta_days_min}-${record.eta_days_max} 天`
      },
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="物流试算"
        subtitle="按起运地、目的地、运输方式试算运费与时效，计费重取实际重量与体积重的较大者"
      />

      <Tabs activeKey={activeKey} onChange={setActiveKey} type="line">
        <Tabs.TabPane tab="试算" itemKey="calc">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 380px) 1fr', gap: 16, marginTop: 16 }}>
            <SectionCard>
              <div className="card-title">试算条件</div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
                <div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>SKU</div>
                  <Select
                    style={{ width: '100%' }}
                    placeholder="选择 SKU"
                    filter={optionMatcher}
                    value={skuId}
                    onChange={(value) => setSkuId(value as number)}
                    optionList={(skusQuery.data ?? []).map((sku) => ({
                      value: sku.id,
                      label: [sku.sku_code, sku.name, sku.specification].filter(Boolean).join(' · '),
                    }))}
                  />
                </div>

                <div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>数量</div>
                  <Input value={quantity} onChange={setQuantity} placeholder="1000" />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>起运地</div>
                    <Input value={origin} onChange={setOrigin} placeholder="如：上海" />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>目的地</div>
                    <Input value={destination} onChange={setDestination} placeholder="如：华东" />
                  </div>
                </div>

                <div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>运输方式</div>
                  <Select
                    style={{ width: '100%' }}
                    placeholder="不填则列出全部方案"
                    showClear
                    value={shippingMethod}
                    onChange={(value) => setShippingMethod(value as string | undefined)}
                    optionList={methodOptions}
                  />
                </div>

                <div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>包装方式</div>
                  <Input value={packageType} onChange={setPackageType} placeholder="仅作记录，如：纸箱" />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                      单件重量 kg（可选）
                    </div>
                    <Input value={weightOverride} onChange={setWeightOverride} placeholder="覆盖 SKU" />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                      单件体积 m³（可选）
                    </div>
                    <Input value={volumeOverride} onChange={setVolumeOverride} placeholder="覆盖 SKU" />
                  </div>
                </div>

                <Button
                  theme="solid"
                  type="primary"
                  block
                  loading={resultQuery.isFetching}
                  disabled={!canCalc}
                  onClick={() => resultQuery.refetch()}
                >
                  试算
                </Button>
                {!canCalc && (
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>请先选择 SKU 并填写大于 0 的数量</div>
                )}
              </div>
            </SectionCard>

            <SectionCard>
              <div className="card-title">试算结果</div>

              {!canCalc && (
                <div style={{ color: 'var(--crm-text-3)', padding: '24px 0' }}>
                  选择 SKU 后这里会显示计费重与各承运商方案。
                </div>
              )}

              {result && (
                <>
                  {(result.warnings ?? []).length > 0 && (
                    <Banner
                      type="warning"
                      description={(result.warnings ?? []).join('；')}
                      style={{ marginTop: 12 }}
                      closeIcon={null}
                    />
                  )}

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: '0 32px', marginTop: 12 }}>
                    <SummaryRow
                      label="实际重量"
                      value={`${result.measures.actual_weight.toLocaleString('zh-CN')} kg`}
                    />
                    <SummaryRow
                      label="体积"
                      value={`${result.measures.volume.toLocaleString('zh-CN')} m³`}
                    />
                    <SummaryRow
                      label="计费重"
                      value={`${result.measures.chargeable_weight.toLocaleString('zh-CN')} kg`}
                      tone="primary"
                    />
                    <SummaryRow label="计费依据" value={result.measures.chargeable_basis} />
                    <SummaryRow
                      label="体积重"
                      value={
                        result.measures.volumetric_enabled
                          ? `${result.measures.volumetric_weight.toLocaleString('zh-CN')} kg`
                          : `未启用（系数 ${result.measures.volumetric_ratio}）`
                      }
                      tone="muted"
                    />
                    <SummaryRow label="体积来源" value={result.measures.volume_source ?? '-'} tone="muted" />
                  </div>

                  <Table
                    style={{ marginTop: 16 }}
                    size="small"
                    rowKey="rate_id"
                    columns={optionColumns}
                    dataSource={result.options}
                    pagination={false}
                    empty="没有匹配到运费费率，请先在价格中心 › 运费费率里维护"
                  />
                </>
              )}
            </SectionCard>
          </div>
        </Tabs.TabPane>

        <Tabs.TabPane tab="试算历史" itemKey="history">
          <Table
            style={{ marginTop: 16 }}
            size="small"
            rowKey="id"
            loading={historyQuery.isLoading}
            columns={historyColumns}
            dataSource={historyQuery.data?.items ?? []}
            pagination={false}
            empty="还没有试算记录"
          />
        </Tabs.TabPane>

        <Tabs.TabPane tab="线路速查" itemKey="routes">
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13, margin: '12px 0' }}>
            线路由运费费率推导而来，维护费率请到「价格中心 › 运费费率」。
          </div>
          <Table
            size="small"
            rowKey={(record?: LogisticsRouteRow) =>
              `${record?.origin}-${record?.destination}-${record?.shipping_method}`
            }
            loading={routesQuery.isLoading}
            dataSource={routesQuery.data ?? []}
            pagination={false}
            empty="还没有维护运费费率"
            columns={routeColumns}
          />
        </Tabs.TabPane>
      </Tabs>
    </div>
  )
}
