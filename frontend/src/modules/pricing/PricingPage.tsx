import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Banner, Button, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import {
  calculatePrice,
  checkPricePermission,
  listSkusForPricing,
  simulatePricing,
  type PricePermissionVerdict,
  type PricingScenario,
  type PricingSimulationResult,
} from '../../shared/api/pricing'
import { getPublicConfig } from '../../shared/api/settings'
import { agentPricingAnalysis, type AnalysisEnvelope } from '../../shared/api/agent'
import { usePermissions } from '../../shared/hooks/permissions'
import AgentInsight from '../../shared/components/AgentInsight'

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'primary' | 'danger' | 'muted' }) {
  const color = tone === 'primary' ? 'var(--crm-primary)' : tone === 'danger' ? 'var(--crm-error)' : 'var(--crm-text)'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--crm-surface-high)' }}>
      <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  )
}

export default function PricingPage() {
  // 支持深链预填：/pricing?sku_id=1&customer_id=1&quantity=3000&quoted_price=25
  const [searchParams] = useSearchParams()
  const paramNumber = (key: string) => {
    const raw = searchParams.get(key)
    return raw && Number.isFinite(Number(raw)) ? Number(raw) : undefined
  }

  const [customerId, setCustomerId] = useState<number | undefined>(paramNumber('customer_id'))
  const [skuId, setSkuId] = useState<number | undefined>(paramNumber('sku_id'))
  const [quantity, setQuantity] = useState(String(paramNumber('quantity') ?? 1000))
  const [logisticsCost, setLogisticsCost] = useState('')
  const [targetMargin, setTargetMargin] = useState('')
  const [quotedPrice, setQuotedPrice] = useState(
    paramNumber('quoted_price') !== undefined ? String(paramNumber('quoted_price')) : '',
  )
  // 外贸口径：默认人民币；只有选了外币，汇率与退税率才出现
  const [currency, setCurrency] = useState('CNY')
  const [exchangeRate, setExchangeRate] = useState('')
  const [taxRefundRate, setTaxRefundRate] = useState('')

  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
  })
  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  // 贸易模式决定要不要显示外贸相关输入：国内模式下整块都不出现
  const configQuery = useQuery({ queryKey: ['public-config'], queryFn: getPublicConfig })
  const exportEnabled = (configQuery.data?.trade_mode ?? 'domestic') !== 'domestic'

  const quantityNumber = Number(quantity)
  const payload = {
    sku_id: skuId,
    quantity: Number.isFinite(quantityNumber) && quantityNumber > 0 ? quantityNumber : 1,
    customer_id: customerId ?? null,
    logistics_cost: logisticsCost.trim() ? Number(logisticsCost) : null,
    target_margin: targetMargin.trim() ? Number(targetMargin) : null,
    quoted_price: quotedPrice.trim() ? Number(quotedPrice) : null,
    // 国内模式下一律按人民币口径核价，不往外传币种与退税
    currency: exportEnabled ? currency : 'CNY',
    exchange_rate: exportEnabled && exchangeRate.trim() ? Number(exchangeRate) : null,
    tax_refund_rate: exportEnabled && taxRefundRate.trim() ? Number(taxRefundRate) : null,
  }

  const resultQuery = useQuery({
    queryKey: ['pricing', payload],
    queryFn: () => calculatePrice(payload),
    enabled: Boolean(skuId),
  })

  // 权限校验：同一个 payload，多回答"这个价能不能自主报、不行要走到哪一级"
  const [verdictVisible, setVerdictVisible] = useState(false)
  const [verdict, setVerdict] = useState<PricePermissionVerdict | null>(null)
  const checkMutation = useMutation({
    mutationFn: () => checkPricePermission(payload),
    onSuccess: (data) => {
      setVerdict(data)
      setVerdictVisible(true)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 报价模拟：一次算多个候选价；留空候选价时后端自动试算建议价 / 授权下限 / 保护价
  const [candidatesText, setCandidatesText] = useState('')
  const [simulation, setSimulation] = useState<PricingSimulationResult | null>(null)
  const simulateMutation = useMutation({
    mutationFn: () =>
      simulatePricing({
        base: payload,
        candidates: candidatesText
          .split(/,|，/)
          .map((item) => Number(item.trim()))
          .filter((item) => Number.isFinite(item) && item > 0),
      }),
    onSuccess: (data) => setSimulation(data),
    onError: (error: Error) => Toast.error(error.message),
  })

  // AI 核价解读（API §37，需 agent:use）：把核价结果翻译成可执行的判断
  const { can } = usePermissions()
  const [aiVisible, setAiVisible] = useState(false)
  const [aiEnvelope, setAiEnvelope] = useState<AnalysisEnvelope | null>(null)
  const aiMutation = useMutation({
    mutationFn: () =>
      agentPricingAnalysis({
        sku_id: payload.sku_id!,
        quantity: payload.quantity,
        customer_id: customerId,
      }),
    onSuccess: (data) => {
      setAiEnvelope(data)
      setAiVisible(true)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const result = resultQuery.data
  const money = (value?: number | null) =>
    value === null || value === undefined ? '-' : `¥${value.toFixed(2)}`
  const percent = (value?: number | null) =>
    value === null || value === undefined ? '-' : `${(value * 100).toFixed(2)}%`

  return (
    <div className="page-container">
      <PageHeader
        title="核价"
        subtitle="填客户、产品、数量，系统按价格中心的成本和价格规则算出建议价与最低允许价"
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(280px, 1fr) minmax(300px, 1fr)', gap: 16 }}>
        <SectionCard title="核价输入">
          <div style={{ display: 'grid', gap: 12 }}>
            <div>
              <div style={{ marginBottom: 4 }}>客户</div>
              <Select
                placeholder="选择客户（可空，仅影响客户等级价与一客一价）"
                value={customerId}
                onChange={(value) => setCustomerId(value as number | undefined)}
                optionList={(customersQuery.data?.items ?? []).map((item) => ({
                  value: item.id,
                  label: `${item.name}（${item.level ?? '-'} 级）`,
                }))}
                filter
                showClear
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>产品 SKU *</div>
              <Select
                placeholder="选择 SKU"
                value={skuId}
                onChange={(value) => setSkuId(value as number)}
                optionList={(skusQuery.data ?? []).map((sku) => ({
                  value: sku.id,
                  label: `${sku.product_name ?? ''} ${sku.sku_code} ${sku.specification ?? ''}`,
                }))}
                filter
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>数量</div>
              <Input value={quantity} onChange={setQuantity} />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>单件运费（留空则按费率表自动估算）</div>
              <Input value={logisticsCost} onChange={setLogisticsCost} placeholder="例如 1.62" />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>目标利润率（留空则用价格规则里的值）</div>
              <Input value={targetMargin} onChange={setTargetMargin} placeholder="例如 0.30" />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>拟报价（填了就会判断这单要不要审批）</div>
              <Input value={quotedPrice} onChange={setQuotedPrice} placeholder="例如 25" />
            </div>
            {exportEnabled && (
              <div>
                <div style={{ marginBottom: 4 }}>报价币种</div>
                <Select
                  value={currency}
                  onChange={(value) => setCurrency(value as string)}
                  optionList={['CNY', 'USD', 'EUR'].map((code) => ({
                    value: code,
                    label: code === 'CNY' ? 'CNY 人民币' : `${code} 外币`,
                  }))}
                  style={{ width: '100%' }}
                />
              </div>
            )}
            {exportEnabled && currency !== 'CNY' && (
              <>
                <div>
                  <div style={{ marginBottom: 4 }}>汇率（1 {currency} 兑人民币）</div>
                  <Input
                    value={exchangeRate}
                    onChange={setExchangeRate}
                    placeholder="例如 7.2"
                  />
                </div>
                <div>
                  <div style={{ marginBottom: 4 }}>出口退税率（没有就留空）</div>
                  <Input
                    value={taxRefundRate}
                    onChange={setTaxRefundRate}
                    placeholder="例如 0.13"
                  />
                </div>
              </>
            )}
          </div>
        </SectionCard>

        <SectionCard title="成本结构（单件）">
          {resultQuery.isLoading && <div style={{ color: 'var(--crm-text-3)' }}>计算中…</div>}
          {result && (
            <>
              <Stat label="采购成本" value={money(result.cost.purchase_cost)} />
              <Stat label="生产成本" value={money(result.cost.production_cost)} />
              <Stat label="包装成本" value={money(result.cost.package_cost)} />
              <Stat label="加工成本" value={money(result.cost.processing_cost)} />
              <Stat label="商品成本小计" value={money(result.cost.goods_cost)} />
              <Stat label="单件运费" value={money(result.cost.logistics_cost)} />
              <Stat label="合计成本" value={money(result.cost.base_cost)} tone="primary" />
              {result.currency && result.currency !== 'CNY' && (
                <>
                  <Stat label={`折${result.currency}成本`} value={result.cost_in_quote_currency?.toFixed(4) ?? '-'} />
                  <Stat label="汇率" value={String(result.exchange_rate ?? '未填')} />
                </>
              )}
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 12 }}>
                成本来源：{result.cost.source}
                {result.customer_price_rule && ' · 命中客户特殊价'}
              </div>
            </>
          )}
          {!skuId && <div style={{ color: 'var(--crm-text-3)' }}>请先选择 SKU</div>}
        </SectionCard>

        <SectionCard
          title="核价结果"
          extra={
            <>
              {can('agent:use') && (
                <Button
                  disabled={!skuId}
                  loading={aiMutation.isPending}
                  onClick={() => aiMutation.mutate()}
                >
                  AI 解读
                </Button>
              )}
              <Button
                disabled={!skuId}
                loading={checkMutation.isPending}
                onClick={() => checkMutation.mutate()}
              >
                权限校验
              </Button>
            </>
          }
        >
          {result && (
            <>
              <Stat label="标准价" value={money(result.standard_price)} />
              <Stat label="建议报价" value={money(result.recommended_price)} tone="primary" />
              <Stat
                label="建议区间"
                value={`${money(result.recommended_range[0])} ~ ${money(result.recommended_range[1])}`}
              />
              <Stat label="最低允许价" value={money(result.minimum_price)} />
              <Stat label="当前报价" value={money(result.quoted_price)} />
              <Stat
                label="预计利润"
                value={money(result.profit)}
                tone={result.profit < 0 ? 'danger' : undefined}
              />
              <Stat
                label="预计利润率"
                value={percent(result.profit_rate)}
                tone={result.profit_rate < 0 ? 'danger' : undefined}
              />
              {result.tax_refund ? (
                <>
                  <Stat label="出口退税（单件）" value={result.tax_refund.toFixed(4)} />
                  <Stat
                    label="退税后利润"
                    value={`${result.profit_with_refund?.toFixed(4)}（${percent(result.profit_rate_with_refund)}）`}
                    tone="primary"
                  />
                </>
              ) : null}
              <Stat label="你的授权最低利润率" value={percent(result.authorized_min_margin)} />
              <div style={{ marginTop: 16 }}>
                {result.approval_required ? (
                  <Tag color="red" size="large">
                    当前价格需要审批
                  </Tag>
                ) : (
                  <Tag color="green" size="large">
                    在当前权限内，可直接报价
                  </Tag>
                )}
              </div>
            </>
          )}
          {!skuId && <div style={{ color: 'var(--crm-text-3)' }}>选择 SKU 后自动计算</div>}
        </SectionCard>
      </div>

      {skuId && (
        <SectionCard
          title="报价模拟（What-if）"
          style={{ marginTop: 16 }}
          extra={
            <>
              <Input
                placeholder="候选价，逗号分隔；留空用关键点位"
                value={candidatesText}
                onChange={setCandidatesText}
                style={{ width: 260 }}
              />
              <Button
                theme="solid"
                loading={simulateMutation.isPending}
                onClick={() => simulateMutation.mutate()}
              >
                开始模拟
              </Button>
            </>
          }
        >
          {!simulation ? (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
              按上面同一组条件一次算多个候选价，看清「能让到哪、再低要审批」；留空候选价时自动试算建议价、授权下限、公司保护价三个点位
            </div>
          ) : (
            <Table<PricingScenario>
              columns={[
                {
                  title: '候选报价',
                  dataIndex: 'quoted_price',
                  width: 120,
                  render: (v: number | null) => money(v),
                },
                { title: '利润', dataIndex: 'profit', width: 110, render: (v: number | null) => money(v) },
                {
                  title: '利润率',
                  dataIndex: 'profit_rate',
                  width: 110,
                  render: (v: number | null) => percent(v),
                },
                {
                  title: '订单金额',
                  dataIndex: 'amount',
                  width: 130,
                  render: (v: number | null) => (v === null ? '-' : `¥${v.toLocaleString('zh-CN')}`),
                },
                {
                  title: '结论',
                  dataIndex: 'approval_required',
                  render: (_: unknown, record: PricingScenario) =>
                    record.approval_required ? (
                      <span style={{ color: 'var(--crm-error)', fontSize: 13 }}>
                        需审批：{record.reasons.join('；') || '超出权限'}
                      </span>
                    ) : (
                      <Tag color="green">可自主报价</Tag>
                    ),
                },
              ]}
              dataSource={simulation.scenarios}
              rowKey="quoted_price"
              pagination={false}
              empty="没有可模拟的价格"
            />
          )}
        </SectionCard>
      )}

      {result && result.warnings.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Banner
            type={result.warnings.some((item) => item.includes('审批')) ? 'warning' : 'info'}
            description={result.warnings.join('；')}
            closeIcon={null}
          />
        </div>
      )}

      <Modal
        title="报价权限校验"
        visible={verdictVisible}
        onCancel={() => setVerdictVisible(false)}
        footer={null}
        width={520}
      >
        {verdict && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              {verdict.allowed ? (
                <Tag color="green" size="large">
                  可以自主报价
                </Tag>
              ) : (
                <Tag color="red" size="large">
                  需要审批
                </Tag>
              )}
              <span style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
                报 {verdict.quoted_price ?? '-'} 元 · {verdict.currency}
              </span>
            </div>
            {verdict.reasons.length > 0 && (
              <div style={{ color: 'var(--crm-error)', fontSize: 13, display: 'grid', gap: 4 }}>
                {verdict.reasons.map((reason) => (
                  <div key={reason}>· {reason}</div>
                ))}
              </div>
            )}
            <div style={{ fontSize: 13, color: 'var(--crm-text-2)', display: 'grid', gap: 6 }}>
              <div>
                最低允许价（你的授权）：{money(verdict.minimum_price)}　·　公司最低保护价：
                {money(verdict.protection_price)}
              </div>
              <div>
                授权最低利润率：
                {verdict.authorized_min_margin != null ? percent(verdict.authorized_min_margin) : '-'}
                （你的角色：{verdict.my_roles.join('、')}）
              </div>
              <div>
                按这个价：利润 {money(verdict.profit)}，利润率 {percent(verdict.profit_rate)}
              </div>
              <div>
                {verdict.approval_required
                  ? verdict.can_approve
                    ? '你自己有审批权，提交后可自行批准'
                    : '需要提交给有报价审批权的人'
                  : '无需审批，可以直接对外发送'}
              </div>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        title="AI 核价解读"
        visible={aiVisible}
        onCancel={() => setAiVisible(false)}
        footer={null}
        width={560}
      >
        <AgentInsight envelope={aiEnvelope} />
      </Modal>
    </div>
  )
}
