import { skuLabel } from '../../shared/sku-label'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Banner, Button, Input, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { formatUnitPrice, formatMoney, toTwoDecimals } from '../../shared/components/money'
import {
  calculatePrice,
  checkPricePermission,
  listSkusForPricing,
  simulatePricing,
  type PricingScenario,
} from '../../shared/api/pricing'
import { agentPricingAnalysis } from '../../shared/api/agent'
import { usePermissions } from '../../shared/hooks/permissions'
import AgentInsight from '../../shared/components/AgentInsight'
import FormLabel from '../../shared/components/FormLabel'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'

function validPricingDecimal(value: string, wholeDigits: number, decimals: number, allowZero = false) {
  if (!/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(value)) return false
  const [whole, fraction = ''] = value.split('.')
  return whole.replace(/^0+/, '').length <= wholeDigits
    && fraction.replace(/0+$/, '').length <= decimals
    && (allowZero || /[1-9]/.test(value))
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'primary' | 'danger' | 'muted' }) {
  const color = tone === 'primary' ? 'var(--crm-primary)' : tone === 'danger' ? 'var(--crm-error)' : 'var(--crm-text)'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--crm-surface-high)' }}>
      <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  )
}

/**
 * 核价本体。不画页头、不套 page-container —— 它既挂在 /pricing 这条深链上，
 * 也从「价格中心」右上角的抽屉里打开（主人 2026-11-21「不用为核价单独做一个大模块呀」：
 * 核价只是价格中心的一个试算工具，不该占一屏、更不该在左侧菜单占一行）。
 * embedded 时容器换窄类名：抽屉只有 860 宽，三列会挤，改两列。
 */
export function PricingTool({ embedded = false }: { embedded?: boolean }) {
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
  const [targetProfitAmount, setTargetProfitAmount] = useState('')
  const { can } = usePermissions()
  const [quotedPrice, setQuotedPrice] = useState(
    paramNumber('quoted_price') !== undefined ? String(paramNumber('quoted_price')) : '',
  )

  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
  })
  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  const amountError = (value: string, wholeDigits: number, decimals: number, allowZero: boolean) =>
    value.trim() && !validPricingDecimal(value.trim(), wholeDigits, decimals, allowZero)
      ? `请输入${allowZero ? '非负' : '大于 0 的'}数字，整数最多 ${wholeDigits} 位，小数最多 ${decimals} 位。`
      : ''

  const quantityError = quantity.trim() ? amountError(quantity, 13, 3, false) : '请填写数量。'
  const logisticsError = amountError(logisticsCost, 12, 4, true)
  const margin = targetMargin.trim()
  const marginError = margin && (!validPricingDecimal(margin, 2, 4) || Number(margin) > 1)
    ? '请输入大于 0 且不超过 1 的小数，最多四位小数。' : ''
  const profitError = can('price:manage') && !margin ? amountError(targetProfitAmount, 14, 2, true) : ''
  const quotedError = amountError(quotedPrice, 12, 4, false)
  const canCalculate = Boolean(skuId && !quantityError && !logisticsError && !marginError && !profitError && !quotedError)
  const payload = {
    sku_id: skuId,
    quantity: quantity.trim(),
    customer_id: customerId ?? null,
    logistics_cost: logisticsCost.trim() || null,
    target_margin: margin || null,
    target_profit_amount: can('price:manage') && !margin ? targetProfitAmount.trim() || null : null,
    quoted_price: quotedPrice.trim() || null,
  }
  const inputKey = JSON.stringify(payload)
  const resultQuery = useQuery({
    queryKey: ['pricing', payload],
    queryFn: () => calculatePrice(payload),
    enabled: canCalculate,
    retry: false,
  })

  const checkMutation = useMutation({
    mutationFn: (_inputKey: string) => checkPricePermission(payload),
    onError: (error: Error) => Toast.error(error.message),
  })
  const verdict = checkMutation.variables === inputKey ? checkMutation.data : undefined

  // 报价模拟：一次算多个候选价；留空候选价时后端自动试算建议价 / 授权下限 / 保护价
  const [candidatesText, setCandidatesText] = useState('')
  const simulateMutation = useMutation({
    mutationFn: (_inputKey: string) => {
      const candidates = candidatesText.split(/[,，]/).map(item => item.trim()).filter(Boolean)
      if (candidates.length > 50 || candidates.some(item => !validPricingDecimal(item, 12, 4))) {
        throw new Error('最多输入 50 个候选价，每个须大于 0，整数最多 12 位，小数最多 4 位。')
      }
      return simulatePricing({ base: payload, candidates })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const simulationKey = JSON.stringify([inputKey, candidatesText])
  const simulation = simulateMutation.variables === simulationKey ? simulateMutation.data : undefined

  const aiMutation = useMutation({
    mutationFn: (_inputKey: string) => agentPricingAnalysis({
      sku_id: payload.sku_id!,
      // The AI endpoint uses an integer quantity, while main pricing accepts decimals.
      quantity: Number(payload.quantity),
      customer_id: customerId,
    }),
    onError: (error: Error) => Toast.error(error.message),
  })
  const aiEnvelope = aiMutation.variables === inputKey ? aiMutation.data ?? null : null
  const aiQuantityValid = Number.isSafeInteger(Number(quantity))

  const result = canCalculate ? resultQuery.data : undefined
  // 核价页显示的都是**单价**（成本/标准价/建议价/最低价），一律保真：
  // 用 toFixed(2) 会把 4 位成本的尾数抹掉，和"单价×数量=金额"对不上（2026-11-17）
  const money = (value?: number | null) =>
    value === null || value === undefined ? '-' : formatUnitPrice(value, 'CNY')
  const percent = (value?: number | null) =>
    value === null || value === undefined ? '-' : `${(value * 100).toFixed(2)}%`

  return (
    <div className={embedded ? 'pricing-grid pricing-grid--drawer' : 'pricing-grid'}>
      <SectionCard title="选择产品和数量">
        <div className="pricing-form">
          <div>
            <FormLabel required>产品</FormLabel>
            <Select aria-label="产品" placeholder="搜索产品名称或编码" value={skuId}
              onChange={value => setSkuId(value as number | undefined)}
              loading={skusQuery.isLoading} showClear filter={optionMatcher} style={{ width: '100%' }}
              optionList={(skusQuery.data ?? []).map(sku => ({ value: sku.id, label: skuLabel(sku) }))} />
            {skusQuery.isError && <Button size="small" type="danger" theme="borderless" onClick={() => void skusQuery.refetch()}>产品列表加载失败，重试</Button>}
          </div>
          <div>
            <FormLabel required>数量</FormLabel>
            <Input aria-label="数量" inputMode="decimal" value={quantity} onChange={setQuantity} validateStatus={quantityError ? 'error' : 'default'} />
            {quantityError && <small style={{ color: 'var(--crm-error)' }}>{quantityError}</small>}
          </div>
          <div>
            <FormLabel hint="选填">客户</FormLabel>
            <Select aria-label="客户" placeholder="选择客户，应用其价格规则" value={customerId}
              onChange={value => setCustomerId(value as number | undefined)}
              optionList={optionsWithCodeOnDuplicate(customersQuery.data?.items ?? [],
                item => `${item.name}（${item.level ?? '-'} 级）`, item => item.id)}
              loading={customersQuery.isLoading} filter={optionMatcher} showClear style={{ width: '100%' }} />
            {customersQuery.isError && <Button size="small" type="danger" theme="borderless" onClick={() => void customersQuery.refetch()}>客户列表加载失败，重试</Button>}
          </div>
          <div>
            <FormLabel hint="选填">拟报价（元／件）</FormLabel>
            <Input aria-label="拟报价" inputMode="decimal" value={quotedPrice} onChange={setQuotedPrice}
              placeholder="填入想报的单价" validateStatus={quotedError ? 'error' : 'default'} />
            <small style={{ color: quotedError ? 'var(--crm-error)' : 'var(--crm-text-3)' }}>
              {quotedError || '填写后可查看该价格的利润和审批要求。'}
            </small>
          </div>
        </div>
        {/* 2026-11-21 主人「不要这种折叠，巨丑」：三个选填条件就三格，
            抽屉里本来放得下（780 宽两列），收进 <details> 之后那一行蓝字加个小三角
            反而像页面被截断了、还得多点一下才能看到运费和利润率。平铺。 */}
        <div className="pricing-form" style={{ marginTop: 16 }}>
            <div>
              <FormLabel>单件运费估算（元）</FormLabel>
              <Input aria-label="单件运费估算" inputMode="decimal" value={logisticsCost} onChange={setLogisticsCost}
                placeholder="例如 1.62" validateStatus={logisticsError ? 'error' : 'default'} />
              <small style={{ color: logisticsError ? 'var(--crm-error)' : 'var(--crm-text-3)' }}>
                {logisticsError || '仅供参考，产品建议价和利润按商品成本计算。'}
              </small>
            </div>
            <div>
              <FormLabel>目标利润率</FormLabel>
              <Input aria-label="目标利润率" inputMode="decimal" value={targetMargin} onChange={setTargetMargin}
                placeholder="例如 0.30 表示售价的 30%" validateStatus={marginError ? 'error' : 'default'} />
              {marginError && <small style={{ color: 'var(--crm-error)' }}>{marginError}</small>}
            </div>
            {/* 七个字段排两列，最后一行只剩它一个 —— 给它跨满，免得右边空半格 */}
            {can('price:manage') && (
              <div style={{ gridColumn: '1 / -1' }}>
                <FormLabel hint="利润率留空时使用">目标单件利润（元）</FormLabel>
                <Input aria-label="目标单件利润" inputMode="decimal" value={targetProfitAmount} onChange={setTargetProfitAmount}
                  disabled={Boolean(margin)} placeholder="例如 80" validateStatus={profitError ? 'error' : 'default'} />
                {profitError && <small style={{ color: 'var(--crm-error)' }}>{profitError}</small>}
              </div>
            )}
        </div>
      </SectionCard>

      {!result && (
        <SectionCard title="试算结果">
          {!skuId ? <div role="status">选择产品并填写数量，系统会自动试算。</div>
            : !canCalculate ? <div role="status" style={{ color: 'var(--crm-error)' }}>
              {quantityError ? '请检查数量。'
                : quotedError ? '请检查拟报价，也可以清空它继续试算。'
                  : logisticsError ? '请检查单件运费，也可以清空它继续试算。'
                    : '请检查目标利润。'}
            </div>
              : resultQuery.isError ? <div role="alert">
                <div>{resultQuery.error.message || '暂时无法完成试算。'}</div>
                <Button size="small" theme="borderless" onClick={() => void resultQuery.refetch()}>重新试算</Button>
              </div> : <div role="status">正在试算…</div>}
        </SectionCard>
      )}
      {result && (
        <SectionCard title="试算结果（元／件）">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px 20px' }}>
            {[
              { label: '建议报价', value: result.recommended_price == null ? '暂无可用建议价' : money(result.recommended_price) },
              ...(can('price:manage') ? [
                { label: '你的最低允许价', value: result.minimum_price == null ? '暂无法计算' : money(result.minimum_price) },
                { label: '公司保护价', value: result.protection_price == null ? '暂未配置' : money(result.protection_price) },
              ] : []),
            ].map(({ label, value }) => (
              <div key={label} style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>{label}</div>
                <strong style={{ fontSize: 21, color: 'var(--crm-primary)', overflowWrap: 'anywhere' }}>{value}</strong>
              </div>
            ))}
          </div>
          {quotedPrice.trim() && <div style={{ marginTop: 12 }}>
            <Tag color={result.approval_required ? 'red' : 'green'}>
              {result.approval_required ? '按拟报价，需要审批' : '按拟报价，可在当前权限内报价'}
            </Tag>
          </div>}
          {result.warnings.length > 0 && <div style={{ marginTop: 12 }}>
            <Banner type="warning" description={result.warnings.join('；')} closeIcon={null} />
          </div>}
        </SectionCard>
      )}

      {result && <div className="pricing-side">
        {can('price:manage') && <SectionCard title="成本结构（单件）" style={{ minHeight: 150 }}>
          <Stat label="采购成本" value={money(result.cost.purchase_cost)} />
          <Stat label="生产成本" value={money(result.cost.production_cost)} />
          <Stat label="包装成本" value={money(result.cost.package_cost)} />
          <Stat label="加工成本" value={money(result.cost.processing_cost)} />
          <Stat label="商品成本小计" value={money(result.cost.goods_cost)} />
          {/* 运费与产品核价分离（2026-10-09）：`base_cost` 现在**只含商品成本**，
              运费不进定价基数。这里如实标出两者的关系，避免用户看到
              "合计成本"跟商品成本一样而以为算错了。 */}
          <Stat label="单件运费（参考估算，不计入核价）" value={money(result.cost.logistics_cost)} />
          <Stat label="产品核价成本（不含运费）" value={money(result.cost.base_cost)} tone="primary" />
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
        </SectionCard>}

        <SectionCard title="报价明细（单件）" style={{ minHeight: 150 }}>
          {/* D3：报价基准是「系统适用价」（专属价→等级价→通用指导价），
              标准价只作让价幅度的对比参照，不作为对外报价口径 */}
          <Stat label="标准价（对比参考）" value={money(result.standard_price)} />
          {result.inputs?.target_profit_price != null && (
            <Stat label="目标利润达标价" value={money(result.inputs.target_profit_price)} />
          )}
          {/* ⚠️ 必须判整体为 null（2026-10-14 审查修）。
              后端 `_sanitize_pricing_result` 对无 `price:manage` 的调用方，
              在"没有规则来源"时是把**整个键**置 null（不是把元素置 null），
              而类型原来写的是 `[number, number]` —— 于是 `null[0]` 抛 TypeError，
              整页被错误边界替换成"这个页面没能加载出来"。
              触发面不小：只有标准价、或新 SKU 还没指导价（建议价按成本推）都会命中。 */}
          <Stat
            label="建议区间"
            value={
              result.recommended_range
                ? `${money(result.recommended_range[0])} ~ ${money(result.recommended_range[1])}`
                : '-'
            }
          />
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
        </SectionCard>

      {/* AI 解读与权限校验：与客户/订单/商机详情页同一个做法 ——
          按钮在卡片右上角、结果在卡片里原地显示。
          原先两处都是弹窗，一层遮罩把底下的核价数据全压暗，
          想「一边看数字一边看解读」就做不到了。 */}
      {can('agent:use') && (
        <SectionCard
          title="AI 核价解读"
          extra={
            <Button
              disabled={!aiQuantityValid}
              loading={aiMutation.isPending}
              onClick={() => aiMutation.mutate(inputKey)}
            >
              AI 解读
            </Button>
          }
        >
          <AgentInsight
            envelope={aiEnvelope}
            empty={aiQuantityValid ? "查看建议价和审批要求的解读。" : "填写整数数量后可使用 AI 解读。"}
          />
        </SectionCard>
      )}

      <SectionCard
        title="报价权限校验"
        extra={
          /* 必带「拟报价」：没填这个价就没什么可校验的，按钮直接禁用
             （而不是让他点了再吃一个 400 回来） */
          <Button
            disabled={!canCalculate || !quotedPrice.trim()}
            loading={checkMutation.isPending}
            onClick={() => checkMutation.mutate(inputKey)}
          >
            权限校验
          </Button>
        }
      >
        {verdict ? (
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
        ) : (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
            填好上方「拟报价」后，可核对允许报价的范围和审批原因。
          </div>
        )}
      </SectionCard>
      </div>}

      {result && (
        <SectionCard
          title="多个报价对比"
          style={{ marginTop: 16 }}
          extra={
            <>
              <Input
                aria-label="候选报价" placeholder="例如 12，15，18"
                value={candidatesText}
                onChange={setCandidatesText}
                style={{ width: 260 }}
              />
              <Button type="primary"
                theme="outline"
                loading={simulateMutation.isPending}
                onClick={() => simulateMutation.mutate(simulationKey)}
              >
                开始模拟
              </Button>
            </>
          }
        >
          {!simulation ? (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
              输入多个单价，用逗号隔开，比较各档利润和审批要求。留空可比较建议报价、最低允许价和公司保护价。
            </div>
          ) : (
            <Table<PricingScenario>
              columns={[
                {
                  title: '候选报价',
                  dataIndex: 'quoted_price',
                  width: 120,
                  // 后端 2026-11-17 起同时给浮点与十进制字符串：浮点可能带
                  // 1.2344999 这种尾数，优先读 quoted_price_exact。
                  render: (_: unknown, record: PricingScenario) =>
                    record.quoted_price_exact != null || record.quoted_price != null
                      ? formatUnitPrice(record.quoted_price_exact ?? record.quoted_price, 'CNY')
                      : '-',
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
                  // 原来 `¥${v.toLocaleString()}`：数量 3、单价 1.2345 显示成「¥3.7」，
                  // 既没有两位小数也没有千分位。金额读 amount_exact（后端逐行到分）。
                  render: (_: unknown, record: PricingScenario) => {
                    const exact = record.amount_exact ?? record.amount
                    return exact == null ? '-' : formatMoney(toTwoDecimals(exact), 'CNY')
                  },
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
              // 浮点单价做 key 会在 1.2345 / 1.23450 这类值上撞车；用精确字符串。
              rowKey={(record?: PricingScenario) =>
                String(record?.quoted_price_exact ?? record?.quoted_price ?? '')
              }
              pagination={false}
              empty="没有可模拟的价格"
            />
          )}
        </SectionCard>
      )}

    </div>
  )
}

export default function PricingPage() {
  return (
    <div className="page-container">
      <PageHeader
        title="核价"
        subtitle="填客户、产品、数量，系统按价格中心的成本和价格规则算出建议价与最低允许价"
      />
      <PricingTool />
    </div>
  )
}
