import { useState } from 'react'
import SampleFromSourceModal from '../sample/SampleFromSourceModal'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Table, Tag, TextArea, Toast } from '@douyinfe/semi-ui'

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
  setQuoteVersionItems,
  submitApproval,
  updateQuoteItem,
  withdrawApproval,
  type QuoteChargeRow,
  type QuoteItemRow,
} from '../../shared/api/quote'
import { listCustomInquiries } from '../../shared/api/inquiry'
import { listSkusForPricing } from '../../shared/api/pricing'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { convertToOrder } from '../../shared/api/order'
import DetailHeader from '../../shared/components/DetailHeader'
import SectionCard from '../../shared/components/SectionCard'
import ContractDocsPanel from '../../shared/components/ContractDocsPanel'
import BizDocPanel from '../../shared/components/BizDocPanel'
import WhatIfPanel from './WhatIfPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'
import { optionMatcher } from '../../shared/components/optionMatch'
import { otherOption } from '../../shared/components/otherOption'

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

/**
 * 报价的发送渠道。
 *
 * 这里的值是**中文本身**（后端 `channel` 也是自由文本），所以选中「其他」后
 * 用户写的内容可以直接存回这个字段。
 *
 * ⚠️ 上面的 `CHARGE_TYPES` 不一样：它的值是 `logistics` 这类**分类码**，
 * 后端拿它查 `CHARGE_LABEL` 生成中文标签，所以费用类型那处**不能**照这个套路改
 * （把"运费补贴"写进 charge_type，这条费用就脱离了原有分类）。
 */
const SEND_CHANNELS = ['邮件', '企业微信', '微信', '其他']

/**
 * 批量录入的一行草稿。现货给 sku_id；定制件给 inquiry_id + 成本 + 报价
 * （定制件没有 SKU，也就没有价格规则可用，成本和报价必须人工给）。
 */
type ItemDraftRow = {
  mode: 'sku' | 'custom'
  sku_id?: number
  inquiry_id?: number
  quantity: string
  quoted_price: string
  unit_cost: string
  remark: string
}

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

  const [draftVisible, setDraftVisible] = useState(false)
  const [sampleVisible, setSampleVisible] = useState(false)
  const [priceTarget, setPriceTarget] = useState<QuoteItemRow | null>(null)
  const [newPrice, setNewPrice] = useState('')
  const [chargeVisible, setChargeVisible] = useState(false)
  const [chargeForm, setChargeForm] = useState({ charge_type: 'logistics', description: '', amount: '' })
  const [sendVisible, setSendVisible] = useState(false)
  const [sendForm, setSendForm] = useState({ channel: '邮件', receiver: '', request_key: '' })

  // 发送渠道的「其他」：选中后多一个输入框写具体渠道（不填就保持「其他」），
  // 写的内容直接存进 channel。
  const channelOption = otherOption({
    options: SEND_CHANNELS,
    value: sendForm.channel,
    onChange: (v) => setSendForm({ ...sendForm, channel: v }),
  })
  const [declineVisible, setDeclineVisible] = useState(false)
  const [declineReason, setDeclineReason] = useState('')
  const [submitVisible, setSubmitVisible] = useState(false)
  const [submitReason, setSubmitReason] = useState('')
  // 批量录入整版明细
  const [itemsVisible, setItemsVisible] = useState(false)
  const [itemRows, setItemRows] = useState<ItemDraftRow[]>([])

  const skusQuery = useQuery({
    queryKey: ['skus-for-pricing'],
    queryFn: listSkusForPricing,
    enabled: itemsVisible,
  })
  const inquiriesQuery = useQuery({
    queryKey: ['inquiries-for-quote'],
    queryFn: () => listCustomInquiries({ page: 1, page_size: 100 }),
    enabled: itemsVisible,
  })

  const patchRow = (index: number, patch: Partial<ItemDraftRow>) => {
    setItemRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  const openItemsEditor = () => {
    const rows = (detail?.items ?? []).map<ItemDraftRow>((item) => ({
      mode: item.is_custom ? 'custom' : 'sku',
      sku_id: item.sku_id ?? undefined,
      inquiry_id: item.inquiry_id ?? undefined,
      quantity: String(item.quantity ?? ''),
      quoted_price: String(item.quoted_price ?? ''),
      // 定制件的成本快照就是当初人工填的核价成本，回填出来方便改
      unit_cost: item.is_custom ? String(item.cost_snapshot ?? '') : '',
      remark: item.remark ?? '',
    }))
    setItemRows(rows.length ? rows : [{ mode: 'sku', quantity: '1', quoted_price: '', unit_cost: '', remark: '' }])
    setItemsVisible(true)
  }

  const itemsMutation = useMutation({
    mutationFn: () =>
      setQuoteVersionItems(
        version!.id,
        itemRows.map((row) => {
          const quantity = Number(row.quantity) || 1
          const price = row.quoted_price.trim() === '' ? null : Number(row.quoted_price)
          if (row.mode === 'custom') {
            return {
              inquiry_id: row.inquiry_id,
              quantity,
              quoted_price: price,
              unit_cost: row.unit_cost.trim() === '' ? null : Number(row.unit_cost),
              remark: row.remark.trim() || null,
            }
          }
          return {
            sku_id: row.sku_id,
            quantity,
            quoted_price: price,
            remark: row.remark.trim() || null,
          }
        }),
      ),
    onSuccess: (updated) => {
      Toast.success('明细已保存（整版替换）')
      // §8.14 复审：明细保存后如实提示"哪些 SKU 的主数据还没确认"
      // （详情页另有一条常驻提示，刷新也在）
      for (const warning of updated?.master_warnings ?? []) {
        Toast.warning({ content: warning, duration: 8 })
      }
      setItemsVisible(false)
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['price-drift', updated?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const convertMutation = useMutation({
    mutationFn: () => convertToOrder(versionId!),
    onSuccess: (data) => {
      Toast.success(`已生成订单 ${data.order_no}`)
      navigate(`/orders/${data.order_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['opportunity'] })
    void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
    void queryClient.invalidateQueries({ queryKey: ['funnel'] })
    void queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-version'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-versions'] })
    void queryClient.invalidateQueries({ queryKey: ['quotes'] })
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['customer'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['customer-quotes'] })
    void queryClient.invalidateQueries({ queryKey: ['timeline'] })
    void queryClient.invalidateQueries({ queryKey: ['followups'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications-unread'] })
  }

  const detail = detailQuery.data
  const version = detail?.version
  const quote = detail?.quote
  const isCurrentVersion = Boolean(version && quote?.current_version_id === version.id)
  const editable = isCurrentVersion && version?.approval_status === 'not_submitted' && !version?.sent_at
  const canRespond = isCurrentVersion && quote?.status === 'sent' && Boolean(version?.sent_at)
    && version?.approval_status === 'approved' && !version.accepted_at && !version.declined_at

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
      // §8.14 复审：改价会重算明细快照，主数据未确认的提醒要跟着出来
      // （详情页另有一条常驻提示，不依赖这次 Toast）
      for (const warning of item.master_warnings ?? []) {
        Toast.warning({ content: warning, duration: 8 })
      }
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
    mutationFn: () => declineQuote(versionId!, declineReason.trim() || undefined),
    onSuccess: () => {
      Toast.success('已记录客户拒绝')
      setDeclineVisible(false)
      setDeclineReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const chargeMutation = useMutation({
    mutationFn: () => {
      const raw = chargeForm.amount.trim()
      const isDiscount = chargeForm.charge_type === 'discount'
      // ⚠️ 不能写 `Number(chargeForm.amount || 0)`：空串会被静默转成 0，
      // 于是一条"还没填金额"的费用会以 **0 元**落库，而 0 元在运费上意味着
      // "明确确认零运费"—— 空输入就被当成了已确认的零元。必须显式拦住。
      if (!raw) {
        throw new Error(
          isDiscount
            ? '请填写优惠金额（空输入不等于 0 元）'
            : '请填写金额；确属零运费也要显式填 0，不能留空当作已确认',
        )
      }
      const amount = Number(raw)
      if (!Number.isFinite(amount) || amount < 0) {
        throw new Error('金额必须是不小于 0 的有效数字')
      }
      if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
        throw new Error('金额最多两位小数')
      }
      return addQuoteCharge(versionId!, {
        charge_type: chargeForm.charge_type,
        description: chargeForm.description,
        amount,
        // 折扣：前端传**正数**，后端负责归一成负数入库（库里恒为负数）
        is_discount: isDiscount,
      })
    },
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

  /**
   * 这一版是不是**旧口径**（`base_cost` 含运费）。
   *
   * 2026-10-09「产品价格与运费分离」：新报价的产品单价不含运费、运费代收代付，
   * 所以成本列不再加单件运费、单价要标"不含运费"。历史版本留在老口径上，
   * 页面显示也必须跟着它自己的口径走 —— 否则会用今天的公式解释当时的数字。
   *
   * 判据取自后端返回的 `pricing_basis`（版本上冻结的），**不在前端猜**。
   */
  const legacyBasis = version.pricing_basis === 'legacy'
  /** 后端算好的金额汇总；老后端没这个键时退回用 version 上的字段，保证不白屏 */
  const summary = detail.summary ?? {
    goods_amount: version.subtotal_amount,
    logistics_amount: version.logistics_amount ?? 0,
    other_charge_amount: version.other_charge_amount ?? 0,
    charge_amount: version.charge_amount,
    discount_amount: version.discount_amount,
    total_amount: version.total_amount,
    pricing_basis: version.pricing_basis,
    pricing_basis_label: version.pricing_basis_label,
  }
  /** 运费未确认的人话原因（后端给）；有值就要一直显示 */
  const freightUnconfirmed = detail.freight_unconfirmed_reason ?? null

  const itemColumns = [
    {
      title: 'SKU / 需求',
      dataIndex: 'sku_code',
      width: 170,
      // 定制项（场景09）没有 SKU：显示需求编号 + 定制标记，
      // 否则这一格会是空的，看的人不知道这条是什么
      // 编码在上、**名称在下**（与订单明细同一口径）。
      // 从前这里只渲染 `sku_code`，而接口早就返回了 `sku_name`
      // （`quote_items.sku_name_snapshot`）—— 满屏 `TP-1210-ST` 这种编码，
      // 看的人不知道是哪件东西（主人 2026-10-10 指出）。
      render: (v: string | null, record: QuoteItemRow) => (
        <div>
          <div>
            {v ?? '-'}
            {record.is_custom && (
              <Tag size="small" type="light" style={{ marginLeft: 6 }}>
                定制
              </Tag>
            )}
          </div>
          {record.sku_name && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{record.sku_name}</div>
          )}
        </div>
      ),
    },
    { title: '规格', dataIndex: 'specification', width: 180, render: (v: string | null) => v ?? '-' },
    { title: '数量', dataIndex: 'quantity', width: 90, render: (v: number) => v.toLocaleString('zh-CN') },
    {
      // 产品核价成本（2026-10-09 运费分离）：新口径下**就是商品成本**，
      // 运费不参与产品定价与产品利润，所以这一格不再加 `logistics_cost_snapshot`。
      // 历史版本（legacy）沿用老口径"商品成本 + 单件运费"，与当时的审批结论一致。
      title: legacyBasis ? '成本快照（含运费）' : '商品成本',
      dataIndex: 'cost_snapshot',
      width: 130,
      render: (v: number, record: QuoteItemRow) =>
        `¥${(legacyBasis ? v + record.logistics_cost_snapshot : v).toFixed(2)}`,
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
      // 单价口径必须在列头写清"不含运费"：运费分离之后，客户与业务员都容易
      // 按含运费的旧口径理解，列头不写就会各理解一套。
      //
      // ⚠️ 但**只在本版确实是"单价不含运费"口径时才写**（2026-10-09 修）：
      // 从前这里硬编码，历史口径的报价也会显示"（不含运费）"，而那一版的单价
      // 里其实是含运费的 —— 等于在界面上改写已发报价的口径。判据用后端返回的
      // `pricing_basis`（版本上冻结的），不在前端猜。
      title: legacyBasis ? '实际报价（含运费）' : '实际报价（不含运费）',
      dataIndex: 'quoted_price',
      width: 150,
      render: (value: number, record: QuoteItemRow) => (
        <span style={{ fontWeight: 600, color: record.approval_required ? 'var(--crm-error)' : 'var(--crm-text)' }}>
          ¥{value}
        </span>
      ),
    },
    {
      // 方案 §4.1"显示价格来源"：拟报价的依据（客户专属价/等级价/通用指导价/手工）
      title: '价格来源',
      dataIndex: 'price_source',
      width: 110,
      render: (v: string | null, record: QuoteItemRow) => {
        const label =
          v === 'customer_specific'
            ? '客户专属价'
            : v === 'level'
              ? '客户等级价'
              : v === 'general'
                ? '通用指导价'
                : '手工价'
        return (
          <Tag type="light" color={v ? 'blue' : 'grey'} size="small">
            {label}
            {record.customer_level_snapshot ? `（${record.customer_level_snapshot}）` : ''}
          </Tag>
        )
      },
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

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    // 下拉里带上 SKU 名称：只有 `编码 · 规格` 时，一个产品下几个 SKU 认不出区别
    label: [sku.sku_code, sku.name, sku.specification].filter(Boolean).join(' · '),
  }))
  const inquiryOptions = (inquiriesQuery.data?.items ?? []).map((row) => ({
    value: row.id,
    label: `${row.inquiry_no ?? `#${row.id}`} · ${row.title}`,
  }))

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
          {canManage && isCurrentVersion && version.approval_status === 'pending' && (
            <Button onClick={() => withdrawMutation.mutate()}>撤回审批</Button>
          )}
          {canManage && isCurrentVersion && version.approval_status === 'approved'
            && (quote.status === 'approved' || quote.status === 'sent') && (
            <Button theme="solid" onClick={() => {
              setSendForm({ ...sendForm, request_key: Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, '0')).join('') })
              setSendVisible(true)
            }}>
              {version.sent_at ? '登记再次发送' : '标记已发送'}
            </Button>
          )}
          {canManage && canRespond && (
            <>
              {/* 客户接受是不可逆的商务事实（后续转订单的前置条件），点一下生效太轻 */}
              <Popconfirm
                title="登记客户已接受这份报价？"
                content="登记后这份报价进入「已接受」，是转销售订单的前置条件。"
                onConfirm={() => acceptMutation.mutate()}
              >
                <Button loading={acceptMutation.isPending}>客户接受</Button>
              </Popconfirm>
              <Button type="danger" onClick={() => setDeclineVisible(true)}>
                客户拒绝
              </Button>
            </>
          )}
          {can('order:manage') && isCurrentVersion && version.sent_at && version.accepted_at && quote.status === 'accepted' && (
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

      {/* §8.14 复审：主数据未确认要**常驻显示** —— 不是一闪而过的 Toast。
          刷新页面、隔天再打开、换个人来看，都该看到"这几条不能用做正式报价"。
          数据来自版本详情接口附带的 `master_warnings`（后端按"明细引用的那一版
          快照里有没有名称/规格/单位"算出来的）。 */}
      {(detail.master_warnings ?? []).length > 0 && (
        <div
          style={{
            marginBottom: 16,
            padding: '10px 14px',
            borderRadius: 6,
            background: 'var(--crm-warning-bg, #FFF7E6)',
            border: '1px solid var(--crm-warning-border, #FFD591)',
            fontSize: 13,
            lineHeight: 1.8,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            这几条明细的主数据还没确认，不能用来做正式报价
          </div>
          {(detail.master_warnings ?? []).map((warning) => (
            <div key={warning}>· {warning}</div>
          ))}
        </div>
      )}

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
      {/* 运费未确认要**持续显示**（与主数据未确认同一性质）：草稿允许没填运费，
          但正式发送会被拦下 —— 等到点"标记已发送"才报错就太晚了。 */}
      {freightUnconfirmed && (
        <div style={{ marginBottom: 16 }}>
          <Tag color="orange" size="large">
            {freightUnconfirmed}
          </Tag>
        </div>
      )}

      <SectionCard
        title="报价明细"
        style={{ marginBottom: 16 }}
        extra={
          <div style={{ display: 'flex', gap: 8 }}>
          {can('order:manage') && <Button size="small" onClick={() => setDraftVisible(true)}>按此版本建订单草稿</Button>}
          {can('sample:manage') && <Button size="small" onClick={() => setSampleVisible(true)}>按此版本申请打样</Button>}
          {canManage &&
          editable && (
            <Button size="small" onClick={openItemsEditor}>
              批量录入
            </Button>
          )}
          </div>
        }
      >
        <Table<QuoteItemRow>
          columns={itemColumns}
          dataSource={detail.items}
          rowKey="id"
          pagination={false}
          scroll={{ x: 1300 }}
          empty="没有明细"
        />
      </SectionCard>

      {draftVisible && <SampleFromSourceModal mode="order" source={{ quote_version_id: version.id }} onClose={() => setDraftVisible(false)} />}
      {sampleVisible && <SampleFromSourceModal source={{ quote_version_id: version.id }} onClose={() => setSampleVisible(false)} />}

      {/* What-if：版本方案对比 + 边际测算（设计稿 Sales Copilot 右栏那个滑杆） */}
      <WhatIfPanel quoteId={quoteId} versionId={version.id} items={detail.items} />

      {/* 两张卡里各有一张表，列宽不能写死 1fr（会被表的固有宽度顶住、整页横向滚动） */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
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
              {
                // 运费要能一眼看出"确认了没有"：金额 0 不等于已确认零运费，
                // 空输入与明确零运费必须分得开（正式发送要求后者）。
                title: '说明',
                dataIndex: 'description',
                render: (v: string | null, record: QuoteChargeRow) => (
                  <span>
                    {v ?? '-'}
                    {record.is_logistics &&
                      (record.logistics_confirmed_at ? (
                        <Tag size="small" type="light" color="green" style={{ marginLeft: 6 }}>
                          运费已确认
                        </Tag>
                      ) : (
                        <Tag size="small" type="light" color="orange" style={{ marginLeft: 6 }}>
                          待确认运费
                        </Tag>
                      ))}
                  </span>
                ),
              },
              {
                title: '金额',
                dataIndex: 'amount',
                width: 120,
                render: (v: number, record: QuoteChargeRow) =>
                  record.is_logistics ? `¥${v.toFixed(2)}（代收代付）` : `¥${v.toFixed(2)}`,
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
            {/* 口径说明（2026-10-09 运费分离）：产品单价不含运费，运费按已确认的
                实际金额代收代付。写在汇总最上方，避免客户/业务员按旧口径理解。
                ⚠️ 同样只在"单价不含运费"口径下显示（见列头的说明）：历史口径的
                单价里含运费，写这句话就是替它改口径。 */}
            {!legacyBasis && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.6 }}>
                产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付
                （公司不赚不赔，产品利润不受运费影响）。
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>产品货款</span>
              <span>¥{summary.goods_amount.toLocaleString('zh-CN')}</span>
            </div>
            {/* 运费从"附加费用"里**单独一行**列具体金额，不再混在一起让客户自己猜。
                注意：下面这几行都**已经含在**应付合计里，只是拆分展示，不再相加。 */}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>运费（代收代付）</span>
              <span>¥{summary.logistics_amount.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>其他费用</span>
              <span>¥{summary.other_charge_amount.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--crm-text-3)' }}>优惠</span>
              <span>¥{summary.discount_amount.toFixed(2)}</span>
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
              <span>应付合计</span>
              <span style={{ color: 'var(--crm-primary)' }}>¥{summary.total_amount.toLocaleString('zh-CN')}</span>
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
        title="批量录入明细"
        visible={itemsVisible}
        onCancel={() => setItemsVisible(false)}
        onOk={() => itemsMutation.mutate()}
        confirmLoading={itemsMutation.isPending}
        okText="保存整版明细"
        width={900}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            保存会「整版替换」这张报价版本的明细（上面那张表会按下面这份重建）。
            现货给 SKU 即可，系统用适用价；定制件必须填成本与报价——没成本算不出毛利，
            也判断不了低价审批。
          </div>
          {itemRows.map((row, index) => (
            <div
              key={index}
              style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}
            >
              <Select
                style={{ width: 96 }}
                value={row.mode}
                onChange={(value) =>
                  patchRow(index, {
                    mode: value as ItemDraftRow['mode'],
                    sku_id: undefined,
                    inquiry_id: undefined,
                  })
                }
                optionList={[
                  { value: 'sku', label: '现货 SKU' },
                  { value: 'custom', label: '定制需求' },
                ]}
              />
              {row.mode === 'custom' ? (
                <Select
                  style={{ width: 240 }}
                  placeholder="选择定制需求"
                  filter={optionMatcher}
                  value={row.inquiry_id}
                  onChange={(value) => patchRow(index, { inquiry_id: value as number })}
                  optionList={inquiryOptions}
                />
              ) : (
                <Select
                  style={{ width: 240 }}
                  placeholder="选择 SKU"
                  filter={optionMatcher}
                  value={row.sku_id}
                  onChange={(value) => patchRow(index, { sku_id: value as number })}
                  optionList={skuOptions}
                />
              )}
              <Input
                style={{ width: 84 }}
                placeholder="数量"
                value={row.quantity}
                onChange={(value) => patchRow(index, { quantity: value })}
              />
              <Input
                style={{ width: 104 }}
                placeholder="报价单价"
                value={row.quoted_price}
                onChange={(value) => patchRow(index, { quoted_price: value })}
              />
              {row.mode === 'custom' && (
                <Input
                  style={{ width: 104 }}
                  placeholder="成本(必填)"
                  value={row.unit_cost}
                  onChange={(value) => patchRow(index, { unit_cost: value })}
                />
              )}
              <Input
                style={{ width: 160 }}
                placeholder="备注"
                value={row.remark}
                onChange={(value) => patchRow(index, { remark: value })}
              />
              <Button
                theme="borderless"
                type="danger"
                onClick={() => setItemRows((rows) => rows.filter((_, i) => i !== index))}
              >
                删除
              </Button>
            </div>
          ))}
          <Button
            theme="borderless"
            onClick={() =>
              setItemRows((rows) => [
                ...rows,
                { mode: 'sku', quantity: '1', quoted_price: '', unit_cost: '', remark: '' },
              ])
            }
          >
            + 添加一行
          </Button>
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
            placeholder={
              chargeForm.charge_type === 'discount'
                ? '优惠金额（填正数，系统自动扣减）'
                : '金额；确属零运费请显式填 0，不要留空'
            }
          />
          {chargeForm.charge_type === 'logistics' && (
            <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.6 }}>
              运费的支出金额与向客户收取的金额<b>共用这一份数据</b>：客户全额承担、
              公司原额代收代付，不赚不赔。填进来即视为已确认的实际运费；
              <b>留空不等于零运费</b>——确实没有运费请显式填 0。
            </div>
          )}
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
        title="记录客户拒绝"
        visible={declineVisible}
        onCancel={() => setDeclineVisible(false)}
        onOk={() => declineMutation.mutate()}
        confirmLoading={declineMutation.isPending}
        okText="确认记录"
      >
        <TextArea value={declineReason} onChange={setDeclineReason}
          placeholder="填写客户实际拒绝原因（可不填）" />
      </Modal>

      <Modal
        title="标记已发送"
        visible={sendVisible}
        onCancel={() => setSendVisible(false)}
        onOk={() => sendMutation.mutate()}
        confirmLoading={sendMutation.isPending}
        okText="确认已实际发送"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Select
            value={channelOption.selectValue}
            onChange={channelOption.onSelect}
            optionList={SEND_CHANNELS.map((value) => ({ value, label: value }))}
            style={{ width: '100%' }}
          />
          {channelOption.showInput && (
            <Input
              value={channelOption.inputValue}
              onChange={channelOption.onInput}
              placeholder="请说明是什么渠道（可不填）"
            />
          )}
          <Input
            value={sendForm.receiver}
            onChange={(value) => setSendForm({ ...sendForm, receiver: value })}
            placeholder="发给谁，例如：李经理 / wang@example.com"
          />
        </div>
      </Modal>

      {/* 这份报价签了什么：合同会钉死"依据的是哪一版"，所以按报价单整单筛，
          不是只看当前版本 —— 早期按 V1 签、现在报价走到 V3，那份合同仍要看得见 */}
      <ContractDocsPanel
        quoteId={quoteId}
        style={{ marginTop: 16 }}
        empty="这份报价还没有关联的合同文档"
      />

      {/* 场景10：对客 Excel 报价单——金额取自当前选中版本的快照，
          之后改价或改模板都不会影响已导出的那份 */}
      <SectionCard title="对外单据（对客报价 Excel）" style={{ marginTop: 16 }}>
        <BizDocPanel
          docType="quote_sheet"
          quoteId={quoteId}
          quoteVersionId={versionId}
          canManage={canManage}
        />
      </SectionCard>
    </div>
  )
}
