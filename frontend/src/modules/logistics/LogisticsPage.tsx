import { skuLabel } from '../../shared/sku-label'
import { formatBeijingTime } from '../../shared/beijing-time'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Banner, Button, Input, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  calculateLogistics,
  listLogisticsQuotes,
  listLogisticsRoutes,
  listSkusForPricing,
  type LogisticsOption,
  type LogisticsQuoteRow,
  type LogisticsRouteRow,
} from '../../shared/api/pricing'
import PageHeader from '../../shared/components/PageHeader'
import HelpTip from '../../shared/components/HelpTip'
import EmptyState, { emptyTextNode } from '../../shared/components/EmptyState'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import { optionMatcher } from '../../shared/components/optionMatch'
import { parseOptionalNumber } from '../../shared/number-input'
import { formatMoney } from '../../shared/components/money'

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

function money(value?: number | string | null) {
  return formatMoney(typeof value === 'number' ? value.toFixed(2) : value, 'CNY')
}

function historyQuantity(row: LogisticsQuoteRow) {
  const value = row.quantity_exact?.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
    ?? row.quantity?.toLocaleString('zh-CN')
  return value == null ? '—' : [value, row.quantity_unit].filter(Boolean).join(' ')
}

export default function LogisticsPage() {
  const [activeKey, setActiveKey] = useState('calc')
  // 试算历史改服务端分页；试算页的空态另取最近 3 条，避免翻到第 3 页时
  // 空态却显示第 3 页的开头三条。
  const HISTORY_PAGE_SIZE = 50
  const [historyPage, setHistoryPage] = useState(1)
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManageRates = can('price:manage')

  // 支持深链预填：/logistics?sku_id=1&quantity=3000&destination=华东&shipping_method=陆运
  // 与核价页保持同一套参数名，商机/报价页以后可以照抄同一段跳转逻辑。
  const [searchParams] = useSearchParams()
  const paramNumber = (key: string) => {
    const raw = searchParams.get(key)
    return raw && Number.isFinite(Number(raw)) ? Number(raw) : undefined
  }

  const [skuId, setSkuId] = useState<number | undefined>(paramNumber('sku_id'))
  const [quantity, setQuantity] = useState(searchParams.get('quantity') || '1000')
  const [origin, setOrigin] = useState(searchParams.get('origin') ?? '')
  const [destination, setDestination] = useState(searchParams.get('destination') ?? '')
  const [shippingMethod, setShippingMethod] = useState<string | undefined>(
    searchParams.get('shipping_method') ?? undefined,
  )
  const [packageType, setPackageType] = useState('')
  // 手工覆盖：分包装或特殊装载时，SKU 上的重量体积不准
  const [weightOverride, setWeightOverride] = useState('')
  const [volumeOverride, setVolumeOverride] = useState('')
  // 包装方式 / 单件重量 / 单件体积 是「可选覆盖」，默认收起。
  // 三项铺开会把「试算」按钮和它下面那句"请先选择 SKU"挤出屏幕 ——
  // 那句话正是解释按钮为什么点不动，看不到就等于没解释。
  // 填了任何一项就强制保持展开，否则用户收起后看不见自己填了什么。
  const [showOverrides, setShowOverrides] = useState(false)
  const overridesOpen = showOverrides || Boolean(packageType || weightOverride || volumeOverride)

  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  const routesQuery = useQuery({ queryKey: ['logistics-routes'], queryFn: listLogisticsRoutes })
  const historyQuery = useQuery({
    queryKey: ['logistics-quotes', historyPage, HISTORY_PAGE_SIZE],
    queryFn: () => listLogisticsQuotes({ page: historyPage, page_size: HISTORY_PAGE_SIZE }),
    enabled: activeKey === 'history',
  })
  // 试算页空态只关心"最近算过的三条"，与历史页的页码无关。
  const recentHistoryQuery = useQuery({
    queryKey: ['logistics-quotes', 'recent'],
    queryFn: () => listLogisticsQuotes({ page: 1, page_size: 3 }),
    enabled: activeKey === 'calc',
  })

  const quantityText = quantity.trim()
  const quantityNumber = Number(quantityText)
  const quantityUnit = skusQuery.data?.find(sku => sku.id === skuId)?.unit?.trim()
  const quantityError = !/^\d*(\.\d+)?$/.test(quantityText) || !Number.isFinite(quantityNumber) || quantityNumber <= 0
    ? '请填写大于 0 的数量。'
    : quantityUnit === '件' && /\.\d*[1-9]/.test(quantityText)
      ? '按件计量，请填写正整数。'
      : !/^\d*(\.\d{0,3}0*)?$/.test(quantityText)
        ? '数量最多保留三位小数。'
        : null
  const weightNumber = parseOptionalNumber(weightOverride)
  const volumeNumber = parseOptionalNumber(volumeOverride)
  const weightInvalid = weightNumber !== null && (!Number.isFinite(weightNumber) || weightNumber <= 0)
  const volumeInvalid = volumeNumber !== null && (!Number.isFinite(volumeNumber) || volumeNumber <= 0)
  const productReady = Boolean(skuId) && !quantityError
  const canCalc = productReady && !weightInvalid && !volumeInvalid

  const payload = useMemo(
    () => ({
      sku_id: skuId,
      quantity: quantityText,
      origin: origin.trim() || null,
      destination: destination.trim() || null,
      shipping_method: shippingMethod ?? null,
      package_type: packageType.trim() || null,
      weight_override: weightInvalid ? null : weightNumber,
      volume_override: volumeInvalid ? null : volumeNumber,
    }),
    [skuId, quantityText, origin, destination, shippingMethod, packageType, weightNumber, volumeNumber, weightInvalid, volumeInvalid],
  )

  const calculation = useMutation({
    mutationFn: (values: typeof payload) => calculateLogistics(values),
  })
  const matchesInputs = canCalc && JSON.stringify(calculation.variables) === JSON.stringify(payload)
  const result = matchesInputs ? calculation.data : undefined
  const calculationError = matchesInputs ? calculation.error : null
  const startCalculation = () => {
    if (canCalc && !calculation.isPending) calculation.mutate(payload)
  }
  const savePlan = useMutation({
    mutationFn: async (option: LogisticsOption) => {
      if (!result || !calculation.variables) throw new Error('请先试算当前条件')
      const saved = await calculateLogistics({
        ...calculation.variables,
        save: true,
        selected_rate_id: option.rate_id,
        expected_amount: option.amount_exact ?? option.amount,
      })
      if (!saved.quoted_id) throw new Error('方案没有保存，请重新试算')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['logistics-quotes'] })
      Toast.success('方案已保存，可在「试算历史」查看')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const referenceOnly = Boolean(result?.options.length && (result.match_level ?? 0) >= 2)
  const matchWarnings = result?.match_warnings ?? []
  const warnings = (result?.warnings ?? []).filter((warning) =>
    !((referenceOnly || !result?.options.length) && matchWarnings.includes(warning)),
  )
  const missingSku = (calculationError as { status?: number } | null)?.status === 404

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
      className: 'cell-money',
      render: (value: number, record: LogisticsOption) => (
        <span style={{ fontWeight: 600 }}>{money(record.amount_exact ?? value)}</span>
      ),
    },
    {
      title: (
        <span className="crm-help-label">
          计价口径
          <HelpTip forLabel="计价口径" text="系统同时按重量和体积算两笔运费，取较大者。标签显示这次实际用的是哪一种。" />
        </span>
      ),
      dataIndex: 'pricing_basis',
      width: 150,
      className: 'cell-money',
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
      title: (
        <span className="crm-help-label">
          最低收费
          <HelpTip forLabel="最低收费" text="每一票的最低运费。算出来低于它时按它收，所以会标「已触发」。" />
        </span>
      ),
      dataIndex: 'min_charge',
      width: 110,
      className: 'cell-money',
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
    {
      title: '操作',
      width: 112,
      render: (_: unknown, option: LogisticsOption) => (
        <Button
          size="small"
          theme="borderless"
          type="primary"
          loading={savePlan.isPending && savePlan.variables?.rate_id === option.rate_id}
          disabled={savePlan.isPending && savePlan.variables?.rate_id !== option.rate_id}
          onClick={() => savePlan.mutate(option)}
        >保存此方案</Button>
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
      render: (_: unknown, record: LogisticsQuoteRow) => historyQuantity(record),
    },
    {
      title: '计费重 (kg)',
      dataIndex: 'chargeable_weight',
      width: 120,
      render: (value: number) => value.toLocaleString('zh-CN'),
    },
    {
      title: '运费', dataIndex: 'amount', width: 110,
      className: 'cell-money',
      render: (value: number, record: LogisticsQuoteRow) => money(record.amount_exact ?? value),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string | null) => (value ? formatBeijingTime(value) : '-'),
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
          {/* 2026-11-20：左栏原先是 minmax(320px,380px)、右栏 1fr ——
              1440 下左卡片 380、右卡片 770，输入框被压得很窄，
              而右边那张只有一句"还没有试算结果"，空得离谱。
              左栏提到 440，两边比例 4:6，表单才不用在窄条里挤。 */}
          <div className="logistics-layout">
            <SectionCard>
              <div className="card-title">试算条件</div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
                {/* 六个字段平铺时看不出"先填什么"，按试算的真实顺序分三段：
                    运什么 → 从哪到哪 → 可选。
                    2026-11-20：第一段原来写"算什么、多少"，"算多少"指数量但读起来像
                    在问算法，改成"运什么、多少件"。 */}
                <div className="calc-step">① 运什么、多少件</div>
                {/* SKU 和数量并排：数量格原来也拉满整列（440 宽的框里放 4 位数），
                    下面的分组因此多占一整行，左卡被撑高、右卡的空态跟着被拉到一样高
                    （2026-11-21 巡检）。SKU 给 2 份、数量 1 份，一行放下两格。 */}
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: 12 }}>
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
                        label: skuLabel(sku),
                      }))}
                    />
                    {skusQuery.isError && <Button size="small" theme="borderless" onClick={() => void skusQuery.refetch()}>重新加载 SKU</Button>}
                  </div>

                  <div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                      {quantityUnit ? `数量（${quantityUnit}）` : '数量'}
                    </div>
                    <Input value={quantity} onChange={setQuantity} placeholder="1000"
                      validateStatus={skuId && quantityError ? 'error' : 'default'} />
                    {skuId && quantityError && <div className="detail-hint">{quantityError}</div>}
                  </div>
                </div>

                <div className="calc-step">② 从哪到哪</div>
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
                    placeholder="全部运输方式"
                    showClear
                    value={shippingMethod}
                    onChange={(value) => setShippingMethod(value as string | undefined)}
                    optionList={methodOptions}
                  />
                </div>

                {overridesOpen && (
                  <>
                    <div className="calc-step">③ 可选：影响计费重</div>
                    <div>
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>包装方式</div>
                      <Input value={packageType} onChange={setPackageType} placeholder="仅作记录，如：纸箱" />
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 12 }}>
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                          单件重量 kg（可选）
                        </div>
                        <Input value={weightOverride} onChange={setWeightOverride} placeholder="覆盖 SKU"
                          aria-label="单件重量" aria-invalid={weightInvalid} inputMode="decimal" />
                        {weightInvalid && <p className="logistics-field-error">单件重量请输入大于 0 的数字，或留空使用 SKU 数据。</p>}
                      </div>
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                          单件体积 m³（可选）
                        </div>
                        <Input value={volumeOverride} onChange={setVolumeOverride} placeholder="覆盖 SKU"
                          aria-label="单件体积" aria-invalid={volumeInvalid} inputMode="decimal" />
                        {volumeInvalid && <p className="logistics-field-error">单件体积请输入大于 0 的数字，或留空使用 SKU 数据。</p>}
                      </div>
                    </div>
                  </>
                )}
                <div style={{ fontSize: 12 }}>
                  <button type="button" className="crm-text-action" onClick={() => setShowOverrides((v) => !v)}>
                    {overridesOpen ? '收起可选设置' : '可选设置：包装方式 / 单件重量 / 单件体积'}
                  </button>
                </div>

                <Button
                  theme="solid"
                  type="primary"
                  block
                  loading={calculation.isPending}
                  disabled={!canCalc}
                  onClick={startCalculation}
                >
                  试算
                </Button>
                {!skuId && (
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>请先选择 SKU</div>
                )}
              </div>
            </SectionCard>

            {/* 右卡要能"把空态放在中间"：卡片被左边那张拉伸到 640 高，
                原来空态只挂 56px 上边距，就贴在卡的上半截、下面空 300 多 px
                （主人 2026-11-21 圈出来："太靠上了，也太空了"）。
                改成纵向 flex + 中间那块 flex:1，空态就落在卡的正中。 */}
            <SectionCard style={{ display: 'flex', flexDirection: 'column' }}>
              <div className="card-title">试算结果</div>

              {calculationError && (
                <EmptyState title="试算失败" hint={calculationError.message || '请稍后再试。'}
                  action={missingSku ? (
                    <Button size="small" theme="outline" onClick={() => setSkuId(undefined)}>重新选择 SKU</Button>
                  ) : (
                    <Button size="small" theme="outline" loading={calculation.isPending} onClick={startCalculation}>
                      重新试算
                    </Button>
                  )} />
              )}
              {matchesInputs && calculation.isPending && <p className="workflow-guide" role="status">正在试算运费…</p>}

              {!matchesInputs && (
                // 「还要填数量」这半句必须留着：只说"选择 SKU"时，光选了 SKU
                // 点不动「试算」，用户会以为坏了。
                <div
                  style={{
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 16,
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '24px 0',
                  }}
                >
                  <EmptyState
                    title="还没有试算结果"
                    hint={canCalc ? '确认左侧条件后，点「试算」查看承运方案。'
                      : productReady ? '修正或清空可选设置后，可继续试算。'
                        : '选好 SKU 和数量后，这里会显示计费重与各承运商方案。'}
                  />
                  {/* 2026-11-21：右卡整片空着（主人说"太空了"）。空态下面接三条
                      "最近算过的"，点一下就把条件填回左边表单 —— 填空，也省得重填。 */}
                  {(recentHistoryQuery.data?.items ?? []).length > 0 && (
                    <div
                      style={{
                        marginTop: 18,
                        width: '100%',
                        maxWidth: 420,
                        margin: '0 auto',
                        borderTop: '1px solid var(--crm-border, #E5E6EB)',
                        paddingTop: 10,
                        textAlign: 'left',
                      }}
                    >
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                        最近保存的方案，点一下带回试算条件
                      </div>
                      {(recentHistoryQuery.data?.items ?? []).slice(0, 3).map((row) => (
                        <div
                          key={row.id}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: 12,
                            padding: '5px 0',
                            fontSize: 12,
                            color: 'var(--crm-text-2)',
                            cursor: 'pointer',
                          }}
                          onClick={() => {
                            if (row.sku_id) setSkuId(row.sku_id)
                            if (row.quantity != null) setQuantity(row.quantity_exact ?? String(row.quantity))
                            setOrigin(row.origin ?? '')
                            setDestination(row.destination ?? '')
                            if (row.shipping_method) setShippingMethod(row.shipping_method)
                          }}
                        >
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {`${row.origin || '—'} → ${row.destination || '—'} · ${historyQuantity(row)}`}
                          </span>
                          <span style={{ flexShrink: 0, color: 'var(--crm-text-3)' }}>
                            {`${money(row.amount_exact ?? row.amount)} · ${row.eta_days != null ? `${row.eta_days} 天` : row.shipping_method}`}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {result && (
                <>
                  {referenceOnly && (
                    <div className="workflow-next logistics-reference">
                      <strong>参考方案</strong>
                      <p className="workflow-guide">{matchWarnings.length
                        ? `${matchWarnings.join('。')}。`
                        : '已放宽部分匹配条件。'}请先和承运商确认线路及费率，再用于报价。</p>
                    </div>
                  )}
                  {warnings.length > 0 && (
                    <Banner
                      type="warning"
                      description={warnings.join('；')}
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

                  {result.options.length > 0 ? (
                    <Table
                      style={{ marginTop: 16 }}
                      size="small"
                      rowKey="rate_id"
                      columns={optionColumns}
                      dataSource={result.options}
                      pagination={false}
                      scroll={{ x: 700 }}
                    />
                  ) : <EmptyState title="没有匹配到运费费率"
                    hint={canManageRates ? '维护对应线路的费率后，可以回来重新试算。' : '请让价格管理员维护对应线路的费率，再回来试算。'}
                    action={canManageRates ? <Link className="logistics-rate-link" to="/prices?tab=logistics">去维护运费费率</Link> : undefined} />}
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
            pagination={{
              currentPage: historyPage,
              pageSize: HISTORY_PAGE_SIZE,
              total: historyQuery.data?.total ?? 0,
              showSizeChanger: false,
              onPageChange: (nextPage: number) => setHistoryPage(nextPage),
            }}
            empty={emptyTextNode(historyQuery, '还没有试算记录')}
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
            empty={emptyTextNode(routesQuery, '还没有维护运费费率')}
            columns={routeColumns}
          />
        </Tabs.TabPane>
      </Tabs>
    </div>
  )
}
