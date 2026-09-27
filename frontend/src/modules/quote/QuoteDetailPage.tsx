import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  acceptQuote,
  addQuoteCharge,
  createQuoteVersion,
  declineQuote,
  deleteQuoteCharge,
  downloadQuotePdf,
  getPriceDrift,
  getQuoteVersion,
  listQuoteVersions,
  markSent,
  refreshPrices,
  submitApproval,
  updateQuoteItem,
  withdrawApproval,
  type QuoteChargeRow,
  type QuoteItemRow,
} from '../../shared/api/quote'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { convertToOrder } from '../../shared/api/order'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import WhatIfPanel from './WhatIfPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'

const STATUS_TONE: Record<string, TagTone> = {
  draft: 'grey',
  pending_approval: 'orange',
  approved: 'blue',
  sent: 'cyan',
  accepted: 'green',
  declined: 'red',
  approval_rejected: 'red',
}

const CHARGE_TYPES = [
  { value: 'logistics', label: '物流' },
  { value: 'packaging', label: '包装' },
  { value: 'tax', label: '税费' },
  { value: 'service', label: '服务费' },
  { value: 'discount', label: '折扣' },
  { value: 'other', label: '其他' },
]

export default function QuoteDetailPage() {
  const params = useParams()
  const quoteId = Number(params.id)
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('quote:manage')

  const versionsQuery = useQuery({
    queryKey: ['quote-versions', quoteId],
    queryFn: () => listQuoteVersions(quoteId),
    enabled: Number.isFinite(quoteId),
  })

  const requestedVersionId = searchParams.get('version') ? Number(searchParams.get('version')) : null
  const versionId = requestedVersionId ?? versionsQuery.data?.[0]?.id

  const detailQuery = useQuery({
    queryKey: ['quote-version', versionId],
    queryFn: () => getQuoteVersion(versionId!),
    enabled: Boolean(versionId),
  })

  const [priceTarget, setPriceTarget] = useState<QuoteItemRow | null>(null)
  const [newPrice, setNewPrice] = useState('')
  const [chargeVisible, setChargeVisible] = useState(false)
  const [chargeForm, setChargeForm] = useState({ charge_type: 'logistics', description: '', amount: '' })
  const [sendVisible, setSendVisible] = useState(false)
  const [sendForm, setSendForm] = useState({ channel: '邮件', receiver: '' })
  const [submitVisible, setSubmitVisible] = useState(false)
  const [submitReason, setSubmitReason] = useState('')

  const convertMutation = useMutation({
    mutationFn: () => convertToOrder(versionId!),
    onSuccess: (data) => {
      Toast.success(`已生成订单 ${data.order_no}`)
      navigate(`/orders/${data.order_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-versions'] })
    void queryClient.invalidateQueries({ queryKey: ['quotes'] })
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
  }

  const detail = detailQuery.data
  const version = detail?.version
  const quote = detail?.quote
  const editable = version?.approval_status === 'not_submitted' && !version?.sent_at

  // A09 后半：草稿版本检测"价格已有更新"（系统带价的明细与当前适用价比对）
  const driftQuery = useQuery({
    queryKey: ['price-drift', version?.id],
    queryFn: () => getPriceDrift(version!.id),
    enabled: Boolean(editable && version),
  })

  const refreshMutation = useMutation({
    mutationFn: () => refreshPrices(version!.id),
    onSuccess: (result) => {
      Toast.success(`已刷新 ${result.refreshed} 条明细（手工价 ${result.skipped} 条未动）`)
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['price-drift', version?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const priceMutation = useMutation({
    mutationFn: () => updateQuoteItem(priceTarget!.id, { quoted_price: Number(newPrice) }),
    onSuccess: (item) => {
      Toast.success(
        item.approval_required ? '已改价：该明细超出你的价格权限，提交后会走审批' : '已改价',
      )
      setPriceTarget(null)
      setNewPrice('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const versionMutation = useMutation({
    mutationFn: () => createQuoteVersion(quoteId),
    onSuccess: (created) => {
      Toast.success(`已创建 V${created.version_no}`)
      setSearchParams({ version: String(created.id) })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const submitMutation = useMutation({
    mutationFn: () => submitApproval(versionId!, submitReason),
    onSuccess: (data) => {
      Toast.success(data.approval_required ? '已提交审批' : '未超出权限，报价已通过，可以发送')
      setSubmitVisible(false)
      setSubmitReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const withdrawMutation = useMutation({
    mutationFn: () => withdrawApproval(versionId!),
    onSuccess: () => {
      Toast.success('已撤回审批')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const sendMutation = useMutation({
    mutationFn: () => markSent(versionId!, sendForm),
    onSuccess: () => {
      Toast.success('已标记为已发送')
      setSendVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const acceptMutation = useMutation({
    mutationFn: () => acceptQuote(versionId!),
    onSuccess: () => {
      Toast.success('客户已接受')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const declineMutation = useMutation({
    mutationFn: () => declineQuote(versionId!, '客户认为价格偏高'),
    onSuccess: () => {
      Toast.success('已记录客户拒绝')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const chargeMutation = useMutation({
    mutationFn: () =>
      addQuoteCharge(versionId!, {
        charge_type: chargeForm.charge_type,
        description: chargeForm.description,
        amount: Number(chargeForm.amount || 0),
        is_discount: chargeForm.charge_type === 'discount',
      }),
    onSuccess: () => {
      Toast.success('已添加附加费用')
      setChargeVisible(false)
      setChargeForm({ charge_type: 'logistics', description: '', amount: '' })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  if (versionsQuery.isLoading || detailQuery.isLoading) {
    return <div className="page-container">加载中…</div>
  }
  if (!detail || !version || !quote) {
    return <div className="page-container">报价单不存在或无权查看</div>
  }

  const itemColumns = [
    { title: 'SKU', dataIndex: 'sku_code', width: 130 },
    { title: '规格', dataIndex: 'specification', width: 180, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 90, render: (v: number) => v.toLocaleString('zh-CN') },
    {
      title: '成本快照',
      dataIndex: 'cost_snapshot',
      width: 110,
      render: (v: number, record: QuoteItemRow) => `¥${(v + record.logistics_cost_snapshot).toFixed(2)}`,
    },
    {
      title: '建议价',
      dataIndex: 'recommended_price_snapshot',
      width: 100,
      render: (v: number | null) => (v === null ? '-' : `¥${v}`),
    },
    {
      title: '最低保护价',
      dataIndex: 'minimum_price_snapshot',
      width: 120,
      render: (v: number | null) => (v === null ? '-' : `¥${v.toFixed(2)}`),
    },
    {
      title: '实际报价',
      dataIndex: 'quoted_price',
      width: 120,
      render: (value: number, record: QuoteItemRow) => (
        <span style={{ fontWeight: 600, color: record.approval_required ? 'var(--crm-error)' : 'var(--crm-text)' }}>
          ¥{value}
        </span>
      ),
    },
    {
      title: '利润率',
      dataIndex: 'profit_rate_snapshot',
      width: 100,
      render: (v: number) => `${(v * 100).toFixed(2)}%`,
    },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 130,
      render: (v: number) => `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '审批',
      dataIndex: 'approval_required',
      width: 110,
      render: (v: boolean) => (v ? <Tag color="orange">需审批</Tag> : <Tag color="green">权限内</Tag>),
    },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record: QuoteItemRow) =>
        canManage && editable ? (
          <a
            style={{ color: 'var(--crm-primary)' }}
            onClick={() => {
              setPriceTarget(record)
              setNewPrice(String(record.quoted_price))
            }}
          >
            改价
          </a>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      {/* 与客户/商机详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={quote.quote_no}
        tags={
          <>
            <Tag color={STATUS_TONE[quote.status] ?? 'grey'}>{quote.status_label}</Tag>
            <Tag>V{version.version_no}</Tag>
            {version.approval_required && <Tag color="orange">超出价格权限</Tag>}
          </>
        }
        meta={
          <>
            <span>
              客户：
              <Link to={`/customers/${quote.customer_id}`} style={{ color: 'var(--crm-primary)' }}>
                {quote.customer_name}
              </Link>
            </span>
            <span>
              商机：
              {quote.opportunity_id ? (
                <Link to={`/opportunities/${quote.opportunity_id}`} style={{ color: 'var(--crm-primary)' }}>
                  {quote.opportunity_title}
                </Link>
              ) : (
                '-'
              )}
            </span>
            <span>负责人：{quote.owner_name ?? '-'}</span>
            <span>有效期至：{quote.valid_until ?? '-'}</span>
            <span>付款条件：{version.payment_terms ?? '-'}</span>
          </>
        }
      >
        {/* 报价动作多，按设计稿放在关键信息行下方单独一行，避免把信息行挤到换行 */}
        <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {canManage && (
            <Button onClick={() => versionMutation.mutate()} loading={versionMutation.isPending}>
              新建版本
            </Button>
          )}
          {canManage && editable && (
            <Button theme="solid" onClick={() => setSubmitVisible(true)}>
              提交审批
            </Button>
          )}
          {canManage && version.approval_status === 'pending' && (
            <Button onClick={() => withdrawMutation.mutate()}>撤回审批</Button>
          )}
          {canManage && version.approval_status === 'approved' && (
            <Button theme="solid" onClick={() => setSendVisible(true)}>
              标记已发送
            </Button>
          )}
          {canManage && (quote.status === 'sent' || quote.status === 'approved') && (
            <>
              <Button onClick={() => acceptMutation.mutate()}>客户接受</Button>
              <Button type="danger" onClick={() => declineMutation.mutate()}>
                客户拒绝
              </Button>
            </>
          )}
          {can('order:manage') && (quote.status === 'accepted' || quote.status === 'sent') && (
            <Button
              theme="solid"
              onClick={() => convertMutation.mutate()}
              loading={convertMutation.isPending}
            >
              转销售订单
            </Button>
          )}
          <Button onClick={() => downloadQuotePdf(version.id, `${quote.quote_no}-V${version.version_no}.pdf`)}>
            下载 PDF
          </Button>
        </div>

        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: '1px solid var(--crm-surface-high)',
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
            alignItems: 'center',
          }}
        >
          <span style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>版本：</span>
          {(versionsQuery.data ?? []).map((item) => (
            <Button
              key={item.id}
              size="small"
              theme={item.id === version.id ? 'solid' : 'borderless'}
              onClick={() => setSearchParams({ version: String(item.id) })}
            >
              V{item.version_no}
            </Button>
          ))}
        </div>
      </DetailHeader>

      {version.approval_status === 'pending' && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="orange" size="large">
            该版本正在审批中，审批通过前不能发送
          </Tag>
        </div>
      )}
      {editable && driftQuery.data?.any_drift && (
        <div
          style={{
            marginBottom: 16,
            background: 'var(--crm-warning-soft, #fff7e6)',
            border: '1px solid var(--crm-warning, #fa8c16)',
            borderRadius: 6,
            padding: '10px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span style={{ flex: 1 }}>
            价格已有更新：{driftQuery.data.items.filter((it) => it.drift).length} 条明细的适用价与当前拟报价不一致
            （手工改价的明细不会被自动覆盖）
          </span>
          <Button
            size="small"
            theme="solid"
            loading={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            一键刷新到最新适用价
          </Button>
        </div>
      )}
      {version.approval_status === 'rejected' && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="red" size="large">
            审批未通过，请调整价格后新建版本重新提交
          </Tag>
        </div>
      )}

      <SectionCard title="报价明细" style={{ marginBottom: 16 }}>
        <Table<QuoteItemRow>
          columns={itemColumns}
          dataSource={detail.items}
          rowKey="id"
          pagination={false}
          scroll={{ x: 1300 }}
          empty="没有明细"
        />
      </SectionCard>

      {/* What-if：版本方案对比 + 边际测算（设计稿 Sales Copilot 右栏那个滑杆） */}
      <WhatIfPanel quoteId={quoteId} versionId={version.id} items={detail.items} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <SectionCard
          title="附加费用"
          extra={
            canManage &&
            editable && (
              <Button size="small" onClick={() => setChargeVisible(true)}>
                添加费用
              </Button>
            )
          }
        >
          <Table<QuoteChargeRow>
            columns={[
              { title: '类型', dataIndex: 'type_label', width: 100 },
              { title: '说明', dataIndex: 'description', render: (v: string | null) => v ?? '-' },
              {
                title: '金额',
                dataIndex: 'amount',
                width: 120,
                render: (v: number) => `¥${v.toFixed(2)}`,
              },
              {
                title: '',
                width: 70,
                render: (_: unknown, record: QuoteChargeRow) =>
                  canManage && editable ? (
                    <Popconfirm
                      title="删除这条费用？"
                      onConfirm={() => deleteQuoteCharge(record.id).then(refresh)}
                    >
                      <a style={{ color: 'var(--crm-error)' }}>删除</a>
                    </Popconfirm>
                  ) : null,
              },
            ]}
            dataSource={detail.charges}
            rowKey="id"
            pagination={false}
            empty="没有附加费用"
          />
        </SectionCard>

        <SectionCard title="金额汇总">
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>商品小计</span>
              <span>¥{version.subtotal_amount.toLocaleString('zh-CN')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>附加费用</span>
              <span>¥{version.charge_amount.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>折扣</span>
              <span>¥{version.discount_amount.toFixed(2)}</span>
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                paddingTop: 10,
                borderTop: '1px solid var(--crm-surface-high)',
                fontWeight: 600,
                fontSize: 16,
              }}
            >
              <span>合计金额</span>
              <span style={{ color: 'var(--crm-primary)' }}>¥{version.total_amount.toLocaleString('zh-CN')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>预计利润合计</span>
              <span>¥{(version.total_profit ?? 0).toFixed(2)}</span>
            </div>
          </div>
        </SectionCard>

        {/* 客户决策关系图（设计稿报价详情右栏），报价要发给谁、谁能拍板都在这 */}
        <DecisionMakerCard customerId={quote.customer_id} title="客户决策关系图" />
      </div>

      {detail.approval && (
        <SectionCard title="审批记录" style={{ marginTop: 16 }}>
          <Table
            columns={[
              { title: '动作', dataIndex: 'action', width: 120,
                render: (v: string) =>
                  ({ submit: '提交申请', approve: '同意', reject: '拒绝', withdraw: '撤回' })[v] ?? v },
              { title: '处理人', dataIndex: 'approver_name', width: 120, render: (v: string | null) => v ?? '-' },
              { title: '意见', dataIndex: 'comment', render: (v: string | null) => v ?? '-' },
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 190,
                render: (v: string) => new Date(v).toLocaleString('zh-CN'),
              },
            ]}
            dataSource={detail.approval.records}
            rowKey="id"
            pagination={false}
          />
        </SectionCard>
      )}

      <Modal
        title={`改价：${priceTarget?.sku_code ?? ''}`}
        visible={Boolean(priceTarget)}
        onCancel={() => setPriceTarget(null)}
        onOk={() => {
          if (!newPrice.trim() || Number(newPrice) <= 0) {
            Toast.warning('请输入有效价格')
            return
          }
          priceMutation.mutate()
        }}
        confirmLoading={priceMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            成本 ¥{(priceTarget ? priceTarget.cost_snapshot + priceTarget.logistics_cost_snapshot : 0).toFixed(2)}
            ｜建议价 ¥{priceTarget?.recommended_price_snapshot}
            ｜最低保护价 ¥{priceTarget?.minimum_price_snapshot?.toFixed(2)}
          </div>
          <Input value={newPrice} onChange={setNewPrice} placeholder="实际报价（元）" />
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            保存后会按你的价格权限重新判断是否需要审批。
          </div>
        </div>
      </Modal>

      <Modal
        title="添加附加费用"
        visible={chargeVisible}
        onCancel={() => setChargeVisible(false)}
        onOk={() => chargeMutation.mutate()}
        confirmLoading={chargeMutation.isPending}
        okText="添加"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Select
            value={chargeForm.charge_type}
            onChange={(value) => setChargeForm({ ...chargeForm, charge_type: value as string })}
            optionList={CHARGE_TYPES}
            style={{ width: '100%' }}
          />
          <Input
            value={chargeForm.description}
            onChange={(value) => setChargeForm({ ...chargeForm, description: value })}
            placeholder="说明，例如：宁波到苏州运费"
          />
          <Input
            value={chargeForm.amount}
            onChange={(value) => setChargeForm({ ...chargeForm, amount: value })}
            placeholder="金额（折扣直接填正数，系统自动扣减）"
          />
        </div>
      </Modal>

      <Modal
        title="提交审批"
        visible={submitVisible}
        onCancel={() => setSubmitVisible(false)}
        onOk={() => submitMutation.mutate()}
        confirmLoading={submitMutation.isPending}
        okText="提交"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            提交后系统会按你的价格权限逐条检查：全部在权限内则直接通过，否则转给主管审批。
          </div>
          <Input
            value={submitReason}
            onChange={setSubmitReason}
            placeholder="申请说明（可不填），例如：客户年度大单要求让价"
          />
        </div>
      </Modal>

      <Modal
        title="标记已发送"
        visible={sendVisible}
        onCancel={() => setSendVisible(false)}
        onOk={() => sendMutation.mutate()}
        confirmLoading={sendMutation.isPending}
        okText="确认发送"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Select
            value={sendForm.channel}
            onChange={(value) => setSendForm({ ...sendForm, channel: value as string })}
            optionList={['邮件', '企业微信', '微信', '其他'].map((value) => ({ value, label: value }))}
            style={{ width: '100%' }}
          />
          <Input
            value={sendForm.receiver}
            onChange={(value) => setSendForm({ ...sendForm, receiver: value })}
            placeholder="发给谁，例如：李经理 / wang@example.com"
          />
        </div>
      </Modal>
    </div>
  )
}
