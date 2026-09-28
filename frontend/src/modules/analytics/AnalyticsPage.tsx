import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import KpiStrip from '../../shared/components/KpiStrip'
import SectionCard from '../../shared/components/SectionCard'
import {
  getCustomerStats,
  getLeadStats,
  getLossStats,
  getOpportunityStats,
  getPaymentStats,
  getPricingStats,
  getProductStats,
  getQuoteStats,
  getReceivableStats,
  getSalesUserStats,
  listSalesTargets,
  upsertSalesTarget,
  type NameValue,
  type ProductStat,
  type SalesTargetRow,
  type SalesUserStat,
} from '../../shared/api/analytics'
import { listUsers } from '../../shared/api/system'
import { usePermissions } from '../../shared/hooks/permissions'

function BarList({ data, unit }: { data: NameValue[]; unit?: string }) {
  if (!data.length) return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
  const max = Math.max(...data.map((item) => item.value), 1)
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {data.map((item) => (
        <div key={item.name}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
            <span>{item.name}</span>
            <span style={{ color: 'var(--crm-text-2)' }}>
              {item.value}
              {unit ?? ''}
            </span>
          </div>
          <div style={{ height: 6, background: 'var(--crm-surface-high)', borderRadius: 3, marginTop: 4 }}>
            <div
              style={{
                width: `${(item.value / max) * 100}%`,
                height: '100%',
                background: 'var(--crm-primary)',
                borderRadius: 3,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function AnalyticsPage() {
  const { can } = usePermissions()
  const canSetTarget = can('settings:manage')
  const queryClient = useQueryClient()
  const [targetYear, setTargetYear] = useState(new Date().getFullYear())
  const [targetModal, setTargetModal] = useState<{
    visible: boolean
    period?: string
    user_id?: number | null
  }>({ visible: false })
  const [targetForm, setTargetForm] = useState<{ user_id?: number | null; period: string; new_customer_target: string; sales_target: string }>({
    user_id: undefined,
    period: `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
    new_customer_target: '',
    sales_target: '',
  })

  const opportunityQuery = useQuery({ queryKey: ['an-opportunities'], queryFn: getOpportunityStats })
  const quoteQuery = useQuery({ queryKey: ['an-quotes'], queryFn: getQuoteStats })
  const customerQuery = useQuery({ queryKey: ['an-customers'], queryFn: getCustomerStats })
  const productQuery = useQuery({ queryKey: ['an-products'], queryFn: getProductStats })
  const salesQuery = useQuery({ queryKey: ['an-sales-users'], queryFn: getSalesUserStats })
  const receivableQuery = useQuery({ queryKey: ['an-receivables'], queryFn: getReceivableStats })
  const lossQuery = useQuery({ queryKey: ['an-losses'], queryFn: getLossStats })
  // PRD §23 补齐的三个维度
  const leadQuery = useQuery({ queryKey: ['an-leads'], queryFn: getLeadStats })
  const pricingQuery = useQuery({ queryKey: ['an-pricing'], queryFn: getPricingStats })
  const paymentQuery = useQuery({ queryKey: ['an-payments'], queryFn: getPaymentStats })
  const targetsQuery = useQuery({
    queryKey: ['sales-targets', targetYear],
    queryFn: () => listSalesTargets(targetYear),
  })
  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: targetModal.visible,
  })
  const targetSaveMutation = useMutation({
    mutationFn: () =>
      upsertSalesTarget({
        period: targetForm.period,
        user_id: targetForm.user_id ?? null,
        new_customer_target: Number(targetForm.new_customer_target || 0),
        sales_target: Number(targetForm.sales_target || 0),
      }),
    onSuccess: () => {
      Toast.success('目标已保存')
      setTargetModal({ visible: false })
      void queryClient.invalidateQueries({ queryKey: ['sales-targets'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openTargetModal = (row?: SalesTargetRow) => {
    if (row) {
      setTargetForm({
        user_id: row.user_id,
        period: row.period,
        new_customer_target: String(row.new_customer_target),
        sales_target: String(row.sales_target),
      })
      setTargetModal({ visible: true, period: row.period, user_id: row.user_id })
    } else {
      setTargetForm({
        user_id: undefined,
        period: `${targetYear}-${String(new Date().getMonth() + 1).padStart(2, '0')}`,
        new_customer_target: '',
        sales_target: '',
      })
      setTargetModal({ visible: true })
    }
  }

  const rate = (actual: number, target: number) => {
    if (!target) return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
    const pct = Math.round((actual / target) * 100)
    const color = pct >= 100 ? 'var(--crm-primary)' : pct >= 70 ? 'var(--crm-caution)' : 'var(--crm-error)'
    return <span style={{ fontWeight: 600, color }}>{pct}%</span>
  }

  const opportunity = opportunityQuery.data
  const quote = quoteQuery.data
  const customer = customerQuery.data
  const receivable = receivableQuery.data
  const lead = leadQuery.data
  const pricing = pricingQuery.data
  const payment = paymentQuery.data
  const cycle = opportunity?.cycle
  const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`

  return (
    <div className="page-container">
      <PageHeader title="数据分析" subtitle="数据从业务流程实时聚合，不做二次录入" />

      <KpiStrip
        items={[
          {
            label: '商机成交率',
            value: `${((opportunity?.win_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `成交 ${opportunity?.won_count ?? 0} / 失单 ${opportunity?.loss_count ?? 0}`,
          },
          {
            label: '报价接受率',
            value: `${((quote?.accept_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `报价单 ${quote?.quote_count ?? 0} 张`,
          },
          {
            label: '平均让价幅度',
            value: `${((quote?.average_discount ?? 0) * 100).toFixed(2)}%`,
            hint: `平均版本数 ${quote?.average_versions ?? 0}`,
          },
          {
            label: '低价审批比例',
            value: `${((quote?.approval_rate ?? 0) * 100).toFixed(0)}%`,
            hint: `需审批 ${quote?.approval_required_count ?? 0} 次`,
          },
          {
            label: '应收合计',
            value: money(receivable?.plan_amount),
            hint: `已收 ${money(receivable?.received_amount)}`,
          },
          {
            label: '未回款',
            value: money(receivable?.unreceived_amount),
            hint: `逾期节点 ${receivable?.overdue_count ?? 0}`,
          },
          {
            label: '线索转化率',
            value: `${((lead?.conversion_rate ?? 0) * 100).toFixed(0)}%`,
            hint:
              lead?.average_conversion_days != null
                ? `平均 ${lead.average_conversion_days} 天转化`
                : `线索 ${lead?.total ?? 0} 条`,
          },
          {
            label: '客户活跃 / 沉睡',
            value: `${customer?.active_count ?? 0} / ${customer?.dormant_count ?? 0}`,
            hint: `复购客户 ${customer?.repeat_customer_count ?? 0} 家`,
          },
          {
            label: '平均成交周期',
            value: cycle?.average_days != null ? `${cycle.average_days} 天` : '-',
            hint: cycle?.won_with_history
              ? `基于 ${cycle.won_with_history} 个有阶段记录的成交商机`
              : '暂无成交商机',
          },
          {
            label: '逾期应收',
            value: money(payment?.overdue_amount),
            hint: `逾期节点 ${payment?.overdue_node_count ?? 0} 个`,
          },
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <SectionCard title="销售漏斗（进行中商机）">
          <BarList
            data={(opportunity?.funnel ?? []).map((row) => ({
              name: row.stage_name,
              value: row.count,
            }))}
            unit=" 个"
          />
        </SectionCard>

        <SectionCard title="商机阶段转化（到达过该阶段的商机数）">
          {(opportunity?.stage_conversion ?? []).length === 0 ? (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>暂无数据</div>
          ) : (
            <Table
              size="small"
              pagination={false}
              rowKey="stage_id"
              dataSource={opportunity?.stage_conversion ?? []}
              columns={[
                { title: '阶段', dataIndex: 'stage_name', width: 130 },
                { title: '到达', dataIndex: 'reached_count', width: 90 },
                {
                  title: '较上一阶段转化',
                  dataIndex: 'conversion_from_previous',
                  render: (v: number | null) =>
                    v === null ? (
                      <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                    ) : (
                      `${(v * 100).toFixed(0)}%`
                    ),
                },
              ]}
            />
          )}
        </SectionCard>

        <SectionCard title="客户来源分布">
          <BarList data={customer?.by_source ?? []} unit=" 家" />
          <div style={{ fontWeight: 600, margin: '20px 0 12px' }}>客户等级分布</div>
          <BarList data={customer?.by_level ?? []} unit=" 家" />
        </SectionCard>

        <SectionCard title="线索来源分布">
          <BarList data={lead?.by_source ?? []} unit=" 条" />
          <div style={{ fontWeight: 600, margin: '20px 0 12px' }}>线索状态分布</div>
          <BarList data={lead?.by_status ?? []} unit=" 条" />
        </SectionCard>

        <SectionCard title="失单原因分布">
          <BarList data={lossQuery.data ?? []} unit=" 单" />
        </SectionCard>

        <SectionCard title="逾期账龄分布（未结清节点）">
          <BarList data={payment?.aging ?? []} unit=" 个" />
        </SectionCard>

        <SectionCard title="回款情况">
          <BarList data={receivable?.by_status ?? []} unit=" 个节点" />
          <div style={{ fontWeight: 600, margin: '20px 0 12px' }}>回款方式分布（金额）</div>
          <BarList
            data={(payment?.by_payment_method ?? []).map((row) => ({
              name: row.name,
              value: Math.round(row.value),
            }))}
            unit=" 元"
          />
        </SectionCard>

        <SectionCard title="价格分析">
          <BarList
            data={(pricing?.average_quoted_price_by_level ?? []).map((row) => ({
              name: `${row.level} 级（${row.item_count} 条）`,
              value: row.average_price,
            }))}
            unit=" 元均价"
          />
          <div style={{ marginTop: 16, fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 2 }}>
            <div>低价审批率：{((pricing?.low_price_approval_rate ?? 0) * 100).toFixed(0)}%</div>
            <div>平均让价：{((pricing?.average_discount_rate ?? 0) * 100).toFixed(2)}%</div>
            <div>最大让价：{((pricing?.max_discount_rate ?? 0) * 100).toFixed(2)}%</div>
          </div>
        </SectionCard>
      </div>

      <SectionCard title="产品表现（询盘 / 报价 / 成交 / 失单 / 利润）" style={{ marginTop: 16 }}>
        <Table<ProductStat>
          columns={[
            { title: 'SKU', dataIndex: 'sku_code', width: 140 },
            { title: '产品', dataIndex: 'product_name', width: 160, render: (v: string | null) => v ?? '-' },
            { title: '规格', dataIndex: 'specification', render: (v: string | null) => v ?? '-' },
            { title: '询盘', dataIndex: 'inquiry_times', width: 80 },
            { title: '报价', dataIndex: 'quote_times', width: 80 },
            {
              title: '累计报价数量',
              dataIndex: 'quote_quantity',
              width: 120,
              render: (v: number) => v.toLocaleString('zh-CN'),
            },
            { title: '成交', dataIndex: 'won_times', width: 80 },
            { title: '失单', dataIndex: 'lost_times', width: 80 },
            {
              title: '利润（快照累计）',
              dataIndex: 'profit_amount',
              width: 150,
              render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
            },
          ]}
          dataSource={productQuery.data ?? []}
          loading={productQuery.isLoading}
          rowKey="sku_code"
          pagination={false}
          empty="还没有报价数据"
        />
      </SectionCard>

      <SectionCard title="业务员表现" style={{ marginTop: 16 }}>
        <Table<SalesUserStat>
          columns={[
            { title: '姓名', dataIndex: 'name', width: 120 },
            { title: '客户数', dataIndex: 'customer_count', width: 90 },
            { title: '跟进数', dataIndex: 'followup_count', width: 90 },
            { title: '商机数', dataIndex: 'opportunity_count', width: 90 },
            { title: '报价数', dataIndex: 'quote_count', width: 90 },
            {
              title: '订单金额',
              dataIndex: 'order_amount',
              width: 150,
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
            {
              title: '回款额（按订单负责人）',
              dataIndex: 'received_amount',
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
          ]}
          dataSource={salesQuery.data ?? []}
          loading={salesQuery.isLoading}
          rowKey="user_id"
          pagination={false}
          empty="暂无数据"
        />
      </SectionCard>

      <SectionCard title="目标 vs 实际（模块⑧）" style={{ marginTop: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <Select
            value={targetYear}
            onChange={(value) => setTargetYear(value as number)}
            optionList={[targetYear - 1, targetYear, targetYear + 1].map((y) => ({
              value: y,
              label: `${y} 年`,
            }))}
            style={{ width: 120 }}
          />
          <div style={{ flex: 1 }} />
          {canSetTarget && <Button theme="solid" onClick={() => openTargetModal()}>设定目标</Button>}
        </div>
        <Table<SalesTargetRow>
          columns={[
            {
              title: '月份',
              dataIndex: 'period',
              width: 100,
              render: (v: string) => v.slice(5) + ' 月',
            },
            { title: '对象', dataIndex: 'user_name', width: 120 },
            { title: '新客目标', dataIndex: 'new_customer_target', width: 100 },
            { title: '新客实际', dataIndex: 'new_customer_actual', width: 100 },
            {
              title: '达成率',
              width: 90,
              render: (_: unknown, r: SalesTargetRow) => rate(r.new_customer_actual, r.new_customer_target),
            },
            {
              title: '销售目标',
              dataIndex: 'sales_target',
              width: 130,
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
            {
              title: '销售实际',
              dataIndex: 'sales_actual',
              width: 130,
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
            {
              title: '达成率',
              width: 90,
              render: (_: unknown, r: SalesTargetRow) => rate(r.sales_actual, r.sales_target),
            },
            ...(canSetTarget
              ? [
                  {
                    title: '操作',
                    width: 80,
                    render: (_: unknown, r: SalesTargetRow) => <a onClick={() => openTargetModal(r)}>编辑</a>,
                  },
                ]
              : []),
          ]}
          dataSource={targetsQuery.data?.rows ?? []}
          loading={targetsQuery.isLoading}
          rowKey={(r?: SalesTargetRow) => `${r?.period}-${r?.user_id ?? 'all'}`}
          pagination={false}
          empty="还没有目标数据"
        />
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--crm-text-3)' }}>
          实际值按月自动统计（销售额 = 非取消订单金额，新客户 = 新建客户档案数）；
          将来聚水潭接入后销售额可切换为出库口径。
        </div>
      </SectionCard>

      <Modal
        title={targetModal.period ? '设定目标' : '设定目标'}
        visible={targetModal.visible}
        onCancel={() => setTargetModal({ visible: false })}
        onOk={() => targetSaveMutation.mutate()}
        confirmLoading={targetSaveMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>对象</div>
            <Select
              value={targetForm.user_id ?? 0}
              onChange={(value) =>
                setTargetForm({ ...targetForm, user_id: value === 0 ? null : (value as number) })
              }
              optionList={[
                { value: 0, label: '全公司' },
                ...(usersQuery.data?.items ?? []).map((u) => ({ value: u.id, label: u.name })),
              ]}
              filter
              style={{ width: '100%' }}
              placeholder="选择全公司或某位业务员"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>月份</div>
            <Select
              value={targetForm.period}
              onChange={(value) => setTargetForm({ ...targetForm, period: value as string })}
              optionList={Array.from({ length: 12 }, (_, i) => {
                const period = `${targetYear}-${String(i + 1).padStart(2, '0')}`
                return { value: period, label: `${targetYear} 年 ${i + 1} 月` }
              })}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>新客户目标（个）</div>
            <Input
              value={targetForm.new_customer_target}
              onChange={(value) => setTargetForm({ ...targetForm, new_customer_target: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>销售额目标（元）</div>
            <Input
              value={targetForm.sales_target}
              onChange={(value) => setTargetForm({ ...targetForm, sales_target: value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
