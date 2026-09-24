import { useQuery } from '@tanstack/react-query'
import { Table } from '@douyinfe/semi-ui'

import {
  getCustomerStats,
  getLossStats,
  getOpportunityStats,
  getProductStats,
  getQuoteStats,
  getReceivableStats,
  getSalesUserStats,
  type NameValue,
  type ProductStat,
  type SalesUserStat,
} from '../../shared/api/analytics'

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

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>{hint}</div>}
    </div>
  )
}

export default function AnalyticsPage() {
  const opportunityQuery = useQuery({ queryKey: ['an-opportunities'], queryFn: getOpportunityStats })
  const quoteQuery = useQuery({ queryKey: ['an-quotes'], queryFn: getQuoteStats })
  const customerQuery = useQuery({ queryKey: ['an-customers'], queryFn: getCustomerStats })
  const productQuery = useQuery({ queryKey: ['an-products'], queryFn: getProductStats })
  const salesQuery = useQuery({ queryKey: ['an-sales-users'], queryFn: getSalesUserStats })
  const receivableQuery = useQuery({ queryKey: ['an-receivables'], queryFn: getReceivableStats })
  const lossQuery = useQuery({ queryKey: ['an-losses'], queryFn: getLossStats })

  const opportunity = opportunityQuery.data
  const quote = quoteQuery.data
  const customer = customerQuery.data
  const receivable = receivableQuery.data
  const money = (value?: number) => `¥${Math.round(value ?? 0).toLocaleString('zh-CN')}`

  return (
    <div className="page-container">
      <h2 className="page-title">数据分析</h2>
      <p className="page-subtitle">数据从业务流程实时聚合，不做二次录入</p>

      <div className="kpi-grid">
        <Stat
          label="商机成交率"
          value={`${((opportunity?.win_rate ?? 0) * 100).toFixed(0)}%`}
          hint={`成交 ${opportunity?.won_count ?? 0} / 失单 ${opportunity?.loss_count ?? 0}`}
        />
        <Stat
          label="报价接受率"
          value={`${((quote?.accept_rate ?? 0) * 100).toFixed(0)}%`}
          hint={`报价单 ${quote?.quote_count ?? 0} 张`}
        />
        <Stat
          label="平均让价幅度"
          value={`${((quote?.average_discount ?? 0) * 100).toFixed(2)}%`}
          hint={`平均版本数 ${quote?.average_versions ?? 0}`}
        />
        <Stat
          label="低价审批比例"
          value={`${((quote?.approval_rate ?? 0) * 100).toFixed(0)}%`}
          hint={`需审批 ${quote?.approval_required_count ?? 0} 次`}
        />
        <Stat
          label="应收合计"
          value={money(receivable?.plan_amount)}
          hint={`已收 ${money(receivable?.received_amount)}`}
        />
        <Stat
          label="未回款"
          value={money(receivable?.unreceived_amount)}
          hint={`逾期节点 ${receivable?.overdue_count ?? 0}`}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div className="card-block">
          <div style={{ fontWeight: 600, marginBottom: 12 }}>销售漏斗（进行中商机）</div>
          <BarList
            data={(opportunity?.funnel ?? []).map((row) => ({
              name: row.stage_name,
              value: row.count,
            }))}
            unit=" 个"
          />
        </div>

        <div className="card-block">
          <div style={{ fontWeight: 600, marginBottom: 12 }}>客户来源分布</div>
          <BarList data={customer?.by_source ?? []} unit=" 家" />
          <div style={{ fontWeight: 600, margin: '20px 0 12px' }}>客户等级分布</div>
          <BarList data={customer?.by_level ?? []} unit=" 家" />
        </div>

        <div className="card-block">
          <div style={{ fontWeight: 600, marginBottom: 12 }}>失单原因分布</div>
          <BarList data={lossQuery.data ?? []} unit=" 单" />
        </div>

        <div className="card-block">
          <div style={{ fontWeight: 600, marginBottom: 12 }}>回款情况</div>
          <BarList data={receivable?.by_status ?? []} unit=" 个节点" />
        </div>
      </div>

      <div className="card-block" style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>产品表现（被报价次数）</div>
        <Table<ProductStat>
          columns={[
            { title: 'SKU', dataIndex: 'sku_code', width: 150 },
            { title: '产品', dataIndex: 'product_name', width: 180, render: (v: string | null) => v ?? '-' },
            { title: '规格', dataIndex: 'specification', render: (v: string | null) => v ?? '-' },
            { title: '被报价次数', dataIndex: 'quote_times', width: 130 },
            {
              title: '累计报价数量',
              dataIndex: 'quote_quantity',
              width: 150,
              render: (v: number) => v.toLocaleString('zh-CN'),
            },
          ]}
          dataSource={productQuery.data ?? []}
          loading={productQuery.isLoading}
          rowKey="sku_code"
          pagination={false}
          empty="还没有报价数据"
        />
      </div>

      <div className="card-block" style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>业务员表现</div>
        <Table<SalesUserStat>
          columns={[
            { title: '姓名', dataIndex: 'name', width: 140 },
            { title: '客户数', dataIndex: 'customer_count', width: 110 },
            { title: '商机数', dataIndex: 'opportunity_count', width: 110 },
            { title: '报价数', dataIndex: 'quote_count', width: 110 },
            {
              title: '订单金额',
              dataIndex: 'order_amount',
              render: (v: number) => `¥${Math.round(v).toLocaleString('zh-CN')}`,
            },
          ]}
          dataSource={salesQuery.data ?? []}
          loading={salesQuery.isLoading}
          rowKey="user_id"
          pagination={false}
          empty="暂无数据"
        />
      </div>
    </div>
  )
}
