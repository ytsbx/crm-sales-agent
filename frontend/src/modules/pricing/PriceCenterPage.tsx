import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Switch, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import {
  createCost,
  createCustomerPriceRule,
  createLogisticsRate,
  createPriceRule,
  deleteCustomerPriceRule,
  disablePriceRule,
  listCosts,
  listCustomerPriceRules,
  listLogisticsRates,
  listPricePermissions,
  listPriceRules,
  listSkusForPricing,
  listPricingHistory,
  savePricePermission,
  type CostRecord,
  type CustomerPriceRow,
  type LogisticsRateRow,
  type PricePermissionRow,
  type PriceRuleRow,
  type PricingHistoryRow,
} from '../../shared/api/pricing'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'

const TABS = [
  { tab: '成本', itemKey: 'costs' },
  { tab: '价格规则', itemKey: 'rules' },
  { tab: '客户特殊价', itemKey: 'customer-prices' },
  { tab: '价格权限', itemKey: 'permissions' },
  { tab: '运费费率', itemKey: 'logistics' },
  { tab: '核价历史', itemKey: 'history' },
]

/** 核价历史里会出现审计日志的 business_type（与后端 /pricing/history 的取值一致）。 */
const HISTORY_TYPE_LABEL: Record<string, string> = {
  quote: '报价单',
  quote_item: '报价明细',
  product_cost: '成本',
  price_rule: '价格规则',
  customer_price_rule: '客户特殊价',
}

/** 把 before/after 差异压成「字段: 旧 → 新」的短句，最多 4 条。 */
function historySummary(row: PricingHistoryRow): string {
  if (row.action === 'create') return '新增'
  if (row.action === 'delete') return '删除'
  const before = row.before ?? {}
  const after = row.after ?? {}
  const parts: string[] = []
  for (const [key, value] of Object.entries(after)) {
    if (parts.length >= 4) {
      parts.push('…')
      break
    }
    if (!(key in before) || String(before[key]) !== String(value)) {
      parts.push(`${key}: ${before[key] ?? '-'} → ${value}`)
    }
  }
  return parts.length ? parts.join('；') : '无字段变化'
}

const money = (value?: number | null) => (value === null || value === undefined ? '-' : `¥${value}`)

export default function PriceCenterPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const canManage = can('price:manage')

  const [activeKey, setActiveKey] = useState('costs')
  const [costSkuId, setCostSkuId] = useState<number | undefined>()
  const [costVisible, setCostVisible] = useState(false)
  const [costForm, setCostForm] = useState({
    purchase_cost: '',
    package_cost: '',
    production_cost: '',
    processing_cost: '',
    effective_from: new Date().toISOString().slice(0, 10),
  })
  const [ruleVisible, setRuleVisible] = useState(false)
  const [ruleForm, setRuleForm] = useState({
    sku_id: null as number | null,
    customer_level: '',
    min_qty: '0',
    standard_price: '',
    guide_price: '',
    minimum_price: '',
    target_margin: '',
  })
  const [customerPriceVisible, setCustomerPriceVisible] = useState(false)
  const [customerPriceForm, setCustomerPriceForm] = useState({
    customer_id: null as number | null,
    sku_id: null as number | null,
    min_qty: '0',
    agreed_price: '',
    minimum_price: '',
  })
  const [permissionTarget, setPermissionTarget] = useState<PricePermissionRow | null>(null)
  const [permissionForm, setPermissionForm] = useState({ minimum_margin: '0.15', can_approve: false })
  const [rateVisible, setRateVisible] = useState(false)
  const [rateForm, setRateForm] = useState({
    provider: '',
    shipping_method: '陆运',
    unit_price_per_kg: '',
    min_charge: '',
    eta_days: '',
  })
  const [historySkuId, setHistorySkuId] = useState<number | undefined>()
  const [historyPage, setHistoryPage] = useState(1)

  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
  })
  const costsQuery = useQuery({
    queryKey: ['costs', costSkuId],
    queryFn: () => listCosts(costSkuId!),
    enabled: Boolean(costSkuId),
  })
  const rulesQuery = useQuery({
    queryKey: ['price-rules'],
    queryFn: () => listPriceRules({ page: 1, page_size: 100 }),
    enabled: activeKey === 'rules',
  })
  const customerPricesQuery = useQuery({
    queryKey: ['customer-price-rules'],
    queryFn: () => listCustomerPriceRules({ page: 1, page_size: 100 }),
    enabled: activeKey === 'customer-prices',
  })
  const permissionsQuery = useQuery({
    queryKey: ['price-permissions'],
    queryFn: listPricePermissions,
    enabled: activeKey === 'permissions',
  })
  const ratesQuery = useQuery({
    queryKey: ['logistics-rates'],
    queryFn: listLogisticsRates,
    enabled: activeKey === 'logistics',
  })
  const historyQuery = useQuery({
    queryKey: ['pricing-history', historyPage, historySkuId],
    queryFn: () =>
      listPricingHistory({ page: historyPage, page_size: 20, sku_id: historySkuId }),
    enabled: activeKey === 'history',
  })

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['costs'] })
    void queryClient.invalidateQueries({ queryKey: ['price-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['customer-price-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['price-permissions'] })
    void queryClient.invalidateQueries({ queryKey: ['logistics-rates'] })
  }

  const costMutation = useMutation({
    mutationFn: () =>
      createCost(costSkuId!, {
        purchase_cost: Number(costForm.purchase_cost || 0),
        package_cost: Number(costForm.package_cost || 0),
        production_cost: Number(costForm.production_cost || 0),
        processing_cost: Number(costForm.processing_cost || 0),
        effective_from: costForm.effective_from,
      }),
    onSuccess: () => {
      Toast.success('成本已保存')
      setCostVisible(false)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const ruleMutation = useMutation({
    mutationFn: () =>
      createPriceRule({
        sku_id: ruleForm.sku_id,
        customer_level: ruleForm.customer_level || null,
        min_qty: Number(ruleForm.min_qty || 0),
        standard_price: ruleForm.standard_price ? Number(ruleForm.standard_price) : null,
        guide_price: ruleForm.guide_price ? Number(ruleForm.guide_price) : null,
        minimum_price: ruleForm.minimum_price ? Number(ruleForm.minimum_price) : null,
        target_margin: ruleForm.target_margin ? Number(ruleForm.target_margin) : null,
      }),
    onSuccess: () => {
      Toast.success('价格规则已创建')
      setRuleVisible(false)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const customerPriceMutation = useMutation({
    mutationFn: () =>
      createCustomerPriceRule({
        customer_id: customerPriceForm.customer_id,
        sku_id: customerPriceForm.sku_id,
        min_qty: Number(customerPriceForm.min_qty || 0),
        agreed_price: Number(customerPriceForm.agreed_price),
        minimum_price: customerPriceForm.minimum_price
          ? Number(customerPriceForm.minimum_price)
          : null,
      }),
    onSuccess: () => {
      Toast.success('客户特殊价已创建')
      setCustomerPriceVisible(false)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const permissionMutation = useMutation({
    mutationFn: () =>
      savePricePermission(permissionTarget!.role_id, {
        minimum_margin: Number(permissionForm.minimum_margin || 0),
        can_approve: permissionForm.can_approve,
      }),
    onSuccess: () => {
      Toast.success('价格权限已保存')
      setPermissionTarget(null)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const rateMutation = useMutation({
    mutationFn: () =>
      createLogisticsRate({
        provider: rateForm.provider,
        shipping_method: rateForm.shipping_method,
        unit_price_per_kg: Number(rateForm.unit_price_per_kg || 0),
        min_charge: Number(rateForm.min_charge || 0),
        eta_days: rateForm.eta_days ? Number(rateForm.eta_days) : null,
      }),
    onSuccess: () => {
      Toast.success('运费费率已创建')
      setRateVisible(false)
      refreshAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    label: `${sku.product_name ?? ''} ${sku.sku_code} ${sku.specification ?? ''}`,
  }))

  return (
    <div className="page-container">
      <PageHeader
        title="价格中心"
        subtitle="核价引擎的全部依据都在这一页：成本、标准价与最低保护价、客户特殊价、各角色的让价权限、运费费率"
        extra={
          <Button theme="solid" onClick={() => navigate('/pricing')}>
            打开核价
          </Button>
        }
      />

      <div className="card-block">
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />

        <div style={{ marginTop: 16 }}>
          {activeKey === 'costs' && (
            <>
              <div className="toolbar">
                <Select
                  placeholder="选择 SKU 查看成本"
                  value={costSkuId}
                  onChange={(value) => setCostSkuId(value as number)}
                  optionList={skuOptions}
                  filter
                  style={{ width: 360 }}
                />
                {canManage && (
                  <Button
                    theme="solid"
                    disabled={!costSkuId}
                    onClick={() => setCostVisible(true)}
                  >
                    新增成本
                  </Button>
                )}
              </div>
              <Table<CostRecord>
                columns={[
                  { title: '生效日期', dataIndex: 'effective_from', width: 130 },
                  { title: '采购', dataIndex: 'purchase_cost', width: 100, render: money },
                  { title: '生产', dataIndex: 'production_cost', width: 100, render: money },
                  { title: '包装', dataIndex: 'package_cost', width: 100, render: money },
                  { title: '加工', dataIndex: 'processing_cost', width: 100, render: money },
                  {
                    title: '合计成本',
                    dataIndex: 'total_cost',
                    width: 120,
                    render: (value: number) => <span style={{ fontWeight: 600 }}>{money(value)}</span>,
                  },
                  { title: '失效日期', dataIndex: 'effective_to', width: 130, render: (v: string | null) => v ?? '生效中' },
                  { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                ]}
                dataSource={costsQuery.data ?? []}
                loading={costsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={costSkuId ? '该 SKU 还没有成本记录' : '请先选择 SKU'}
              />
            </>
          )}

          {activeKey === 'rules' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button theme="solid" onClick={() => setRuleVisible(true)}>
                    新增价格规则
                  </Button>
                )}
              </div>
              <Table<PriceRuleRow>
                columns={[
                  { title: 'SKU', dataIndex: 'sku_code', width: 140 },
                  {
                    title: '客户等级',
                    dataIndex: 'customer_level',
                    width: 100,
                    render: (v: string | null) => v ?? '全部',
                  },
                  {
                    title: '数量区间',
                    width: 160,
                    render: (_: unknown, record: PriceRuleRow) =>
                      `${record.min_qty} ~ ${record.max_qty ?? '不限'}`,
                  },
                  { title: '标准价', dataIndex: 'standard_price', width: 100, render: money },
                  { title: '指导价', dataIndex: 'guide_price', width: 100, render: money },
                  {
                    title: '最低保护价',
                    dataIndex: 'minimum_price',
                    width: 120,
                    render: (value: number | null) => (
                      <span style={{ fontWeight: 600, color: 'var(--crm-error)' }}>{money(value)}</span>
                    ),
                  },
                  {
                    title: '目标利润率',
                    dataIndex: 'target_margin',
                    width: 110,
                    render: (v: number | null) => (v === null ? '-' : `${(v * 100).toFixed(0)}%`),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 90,
                    render: (v: string) => (v === 'active' ? <Tag color="green">生效</Tag> : <Tag>停用</Tag>),
                  },
                  {
                    title: '操作',
                    width: 80,
                    render: (_: unknown, record: PriceRuleRow) =>
                      canManage && record.status === 'active' ? (
                        <Popconfirm title="停用这条价格规则？" onConfirm={() => disablePriceRule(record.id).then(refreshAll)}>
                          <a style={{ color: 'var(--crm-error)' }}>停用</a>
                        </Popconfirm>
                      ) : (
                        '-'
                      ),
                  },
                ]}
                dataSource={rulesQuery.data?.items ?? []}
                loading={rulesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有价格规则"
                scroll={{ x: 1100 }}
              />
            </>
          )}

          {activeKey === 'customer-prices' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button theme="solid" onClick={() => setCustomerPriceVisible(true)}>
                    新增客户特殊价
                  </Button>
                )}
              </div>
              <Table<CustomerPriceRow>
                columns={[
                  { title: '客户', dataIndex: 'customer_name', width: 240, render: (v: string | null) => v ?? '-' },
                  { title: 'SKU', dataIndex: 'sku_code', width: 140 },
                  {
                    title: '起订量',
                    dataIndex: 'min_qty',
                    width: 110,
                  },
                  {
                    title: '约定价',
                    dataIndex: 'agreed_price',
                    width: 110,
                    render: (value: number) => <span style={{ fontWeight: 600 }}>{money(value)}</span>,
                  },
                  { title: '最低价', dataIndex: 'minimum_price', width: 110, render: money },
                  { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                  {
                    title: '操作',
                    width: 80,
                    render: (_: unknown, record: CustomerPriceRow) =>
                      canManage ? (
                        <Popconfirm
                          title="删除这条客户特殊价？"
                          onConfirm={() => deleteCustomerPriceRule(record.id).then(refreshAll)}
                        >
                          <a style={{ color: 'var(--crm-error)' }}>删除</a>
                        </Popconfirm>
                      ) : (
                        '-'
                      ),
                  },
                ]}
                dataSource={customerPricesQuery.data?.items ?? []}
                loading={customerPricesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有客户特殊价"
              />
            </>
          )}

          {activeKey === 'permissions' && (
            <Table<PricePermissionRow>
              columns={[
                { title: '角色', dataIndex: 'role_name', width: 160 },
                { title: '角色码', dataIndex: 'role_code', width: 160, render: (v: string | null) => v ?? '-' },
                {
                  title: '最低利润率',
                  dataIndex: 'minimum_margin',
                  width: 140,
                  render: (value: number) => `${(value * 100).toFixed(0)}%`,
                },
                {
                  title: '最大折扣',
                  dataIndex: 'discount_limit',
                  width: 120,
                  render: (v: number | null) => (v === null ? '-' : `${(v * 100).toFixed(0)}%`),
                },
                {
                  title: '可审批报价',
                  dataIndex: 'can_approve',
                  width: 120,
                  render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
                },
                { title: '说明', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                {
                  title: '操作',
                  width: 80,
                  render: (_: unknown, record: PricePermissionRow) =>
                    canManage ? (
                      <a
                        style={{ color: 'var(--crm-primary)' }}
                        onClick={() => {
                          setPermissionTarget(record)
                          setPermissionForm({
                            minimum_margin: String(record.minimum_margin),
                            can_approve: record.can_approve,
                          })
                        }}
                      >
                        调整
                      </a>
                    ) : (
                      '-'
                    ),
                },
              ]}
              dataSource={permissionsQuery.data ?? []}
              loading={permissionsQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}

          {activeKey === 'logistics' && (
            <>
              <div className="toolbar">
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  运费用于核价里的单件运费估算，也可以核价时手工覆盖
                </div>
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button theme="solid" onClick={() => setRateVisible(true)}>
                    新增运费费率
                  </Button>
                )}
              </div>
              <Table<LogisticsRateRow>
                columns={[
                  { title: '承运方式', dataIndex: 'provider' },
                  { title: '运输方式', dataIndex: 'shipping_method', width: 120 },
                  { title: '目的地', dataIndex: 'destination_region', width: 160, render: (v: string | null) => v ?? '-' },
                  {
                    title: '公斤单价',
                    dataIndex: 'unit_price_per_kg',
                    width: 120,
                    render: (value: number) => `¥${value}`,
                  },
                  {
                    title: '最低收费',
                    dataIndex: 'min_charge',
                    width: 120,
                    render: (value: number) => `¥${value}`,
                  },
                  { title: '时效（天）', dataIndex: 'eta_days', width: 110, render: (v: number | null) => v ?? '-' },
                ]}
                dataSource={ratesQuery.data ?? []}
                loading={ratesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有运费费率"
              />
            </>
          )}

          {activeKey === 'history' && (
            <>
              <div className="toolbar">
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  报价、成本与价格规则的全部变更（取自审计日志），按 SKU 过滤可追溯一价一改
                </div>
                <div style={{ flex: 1 }} />
                <Select
                  placeholder="按 SKU 过滤（可空）"
                  value={historySkuId}
                  onChange={(value) => {
                    setHistorySkuId(value as number | undefined)
                    setHistoryPage(1)
                  }}
                  optionList={skuOptions}
                  filter
                  showClear
                  style={{ width: 320 }}
                />
              </div>
              <Table<PricingHistoryRow>
                columns={[
                  {
                    title: '时间',
                    dataIndex: 'created_at',
                    width: 170,
                    render: (v: string) => new Date(v).toLocaleString('zh-CN'),
                  },
                  {
                    title: '类型',
                    dataIndex: 'business_type',
                    width: 110,
                    render: (v: string | null) => HISTORY_TYPE_LABEL[v ?? ''] ?? v ?? '-',
                  },
                  { title: '对象 ID', dataIndex: 'business_id', width: 90 },
                  {
                    title: '动作',
                    dataIndex: 'action',
                    width: 90,
                    render: (v: string) =>
                      ({ create: '新增', update: '修改', delete: '删除' })[v] ?? v,
                  },
                  {
                    title: '变更内容',
                    dataIndex: 'after',
                    render: (_: unknown, record: PricingHistoryRow) => historySummary(record),
                  },
                  {
                    title: '操作人',
                    dataIndex: 'operator_name',
                    width: 110,
                    render: (v: string | null) => v ?? '-',
                  },
                ]}
                dataSource={historyQuery.data?.items ?? []}
                loading={historyQuery.isLoading}
                rowKey="id"
                pagination={{
                  currentPage: historyPage,
                  pageSize: 20,
                  total: historyQuery.data?.total ?? 0,
                  onPageChange: setHistoryPage,
                }}
                empty="还没有变更记录"
              />
            </>
          )}
        </div>
      </div>

      <Modal
        title="新增成本"
        visible={costVisible}
        onCancel={() => setCostVisible(false)}
        onOk={() => costMutation.mutate()}
        confirmLoading={costMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {(
            [
              ['purchase_cost', '采购成本'],
              ['production_cost', '生产成本'],
              ['package_cost', '包装成本'],
              ['processing_cost', '加工成本'],
            ] as const
          ).map(([field, label]) => (
            <div key={field}>
              <div style={{ marginBottom: 4 }}>{label}（元）</div>
              <Input
                value={costForm[field]}
                onChange={(value) => setCostForm({ ...costForm, [field]: value })}
                placeholder="留空按 0 处理"
              />
            </div>
          ))}
          <div>
            <div style={{ marginBottom: 4 }}>生效日期</div>
            <Input
              value={costForm.effective_from}
              onChange={(value) => setCostForm({ ...costForm, effective_from: value })}
              placeholder="2026-01-01"
            />
          </div>
        </div>
      </Modal>

      <Modal
        title="新增价格规则"
        visible={ruleVisible}
        width={620}
        onCancel={() => setRuleVisible(false)}
        onOk={() => {
          if (!ruleForm.sku_id) {
            Toast.warning('请选择 SKU')
            return
          }
          ruleMutation.mutate()
        }}
        confirmLoading={ruleMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>SKU *</div>
            <Select
              value={ruleForm.sku_id ?? undefined}
              onChange={(value) => setRuleForm({ ...ruleForm, sku_id: value as number })}
              optionList={skuOptions}
              filter
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户等级（留空 = 全部等级）</div>
              <Select
                value={ruleForm.customer_level || undefined}
                onChange={(value) => setRuleForm({ ...ruleForm, customer_level: (value as string) ?? '' })}
                optionList={['A', 'B', 'C', 'D'].map((value) => ({ value, label: `${value} 级` }))}
                showClear
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>起订数量</div>
              <Input
                value={ruleForm.min_qty}
                onChange={(value) => setRuleForm({ ...ruleForm, min_qty: value })}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>标准价</div>
              <Input
                value={ruleForm.standard_price}
                onChange={(value) => setRuleForm({ ...ruleForm, standard_price: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>指导价</div>
              <Input
                value={ruleForm.guide_price}
                onChange={(value) => setRuleForm({ ...ruleForm, guide_price: value })}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>最低保护价</div>
              <Input
                value={ruleForm.minimum_price}
                onChange={(value) => setRuleForm({ ...ruleForm, minimum_price: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>目标利润率（如 0.30）</div>
              <Input
                value={ruleForm.target_margin}
                onChange={(value) => setRuleForm({ ...ruleForm, target_margin: value })}
              />
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title="新增客户特殊价"
        visible={customerPriceVisible}
        onCancel={() => setCustomerPriceVisible(false)}
        onOk={() => {
          if (!customerPriceForm.customer_id || !customerPriceForm.sku_id || !customerPriceForm.agreed_price) {
            Toast.warning('客户、SKU、约定价都要填')
            return
          }
          customerPriceMutation.mutate()
        }}
        confirmLoading={customerPriceMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>客户 *</div>
            <Select
              value={customerPriceForm.customer_id ?? undefined}
              onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, customer_id: value as number })}
              optionList={(customersQuery.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))}
              filter
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>SKU *</div>
            <Select
              value={customerPriceForm.sku_id ?? undefined}
              onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, sku_id: value as number })}
              optionList={skuOptions}
              filter
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>起订量</div>
              <Input
                value={customerPriceForm.min_qty}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, min_qty: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>约定价 *</div>
              <Input
                value={customerPriceForm.agreed_price}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, agreed_price: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>最低价</div>
              <Input
                value={customerPriceForm.minimum_price}
                onChange={(value) => setCustomerPriceForm({ ...customerPriceForm, minimum_price: value })}
              />
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title={`调整价格权限：${permissionTarget?.role_name ?? ''}`}
        visible={Boolean(permissionTarget)}
        onCancel={() => setPermissionTarget(null)}
        onOk={() => permissionMutation.mutate()}
        confirmLoading={permissionMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>最低利润率（0.15 = 15%）</div>
            <Input
              value={permissionForm.minimum_margin}
              onChange={(value) => setPermissionForm({ ...permissionForm, minimum_margin: value })}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Switch
              checked={permissionForm.can_approve}
              onChange={(value) => setPermissionForm({ ...permissionForm, can_approve: value })}
            />
            <span>该角色可以审批低价报价</span>
          </div>
        </div>
      </Modal>

      <Modal
        title="新增运费费率"
        visible={rateVisible}
        onCancel={() => setRateVisible(false)}
        onOk={() => {
          if (!rateForm.provider.trim()) {
            Toast.warning('请填写承运方式')
            return
          }
          rateMutation.mutate()
        }}
        confirmLoading={rateMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>承运方式 *</div>
            <Input
              value={rateForm.provider}
              onChange={(value) => setRateForm({ ...rateForm, provider: value })}
              placeholder="例如：德邦零担"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>公斤单价（元）</div>
              <Input
                value={rateForm.unit_price_per_kg}
                onChange={(value) => setRateForm({ ...rateForm, unit_price_per_kg: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>最低收费（元）</div>
              <Input
                value={rateForm.min_charge}
                onChange={(value) => setRateForm({ ...rateForm, min_charge: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>时效（天）</div>
              <Input
                value={rateForm.eta_days}
                onChange={(value) => setRateForm({ ...rateForm, eta_days: value })}
              />
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}
