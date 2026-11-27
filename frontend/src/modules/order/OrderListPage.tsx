import { skuLabel } from '../../shared/sku-label'
import { useEffect, useRef, useState } from 'react'
import { newRequestKey } from '../../shared/api/requestKey'
import { requiredQuantityText } from '../../shared/number-input'
import { paymentMoney } from '../../shared/payment-allocation'
import PageHeader from '../../shared/components/PageHeader'
import HelpTip from '../../shared/components/HelpTip'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyTextNode } from '../../shared/components/EmptyState'
import { Button, DatePicker, Input, Modal, Popconfirm, Select, Table, Tabs, Toast } from '@douyinfe/semi-ui'

import {
  confirmPayment,
  createOrder,
  listOrders,
  listPayments,
  listReceivables,
  rejectPayment,
  type Order,
  type Payment,
  type Receivable,
} from '../../shared/api/order'
import { getCustomer, listCustomers } from '../../shared/api/customer'
import { listSkusForPricing } from '../../shared/api/pricing'
import { useAuthStore } from '../../shared/store/auth'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import StatusDot from '../../shared/components/StatusDot'
import SectionCard from '../../shared/components/SectionCard'
import PaymentVoucherControl from '../common/PaymentVoucherControl'
import FormLabel from '../../shared/components/FormLabel'
import { optionMatcher } from '../../shared/components/optionMatch'
import RecordSearchSelect from '../../shared/components/RecordSearchSelect'
import UserSearchSelect from '../../shared/components/UserSearchSelect'
// 金额一律带**实际币种**（第九批 §9.9）：列表以前写死 `¥`，
// 有外币订单时显示的就是错的数字。
import { formatMoney, toTwoDecimals } from '../../shared/components/money'

const TABS = [
  { tab: '销售订单', itemKey: 'orders', permission: 'order:view' },
  { tab: '应收计划', itemKey: 'receivables', permission: 'payment:view' },
  { tab: '回款记录', itemKey: 'payments', permission: 'payment:view' },
]

const ORDER_TONE: Record<string, TagTone> = {
  pending: 'grey',
  in_production: 'blue',
  shipped: 'cyan',
  delivered: 'violet',
  completed: 'green',
  cancelled: 'grey',
}

const PLAN_TONE: Record<string, TagTone> = {
  pending: 'grey',
  partial: 'orange',
  paid: 'green',
  overdue: 'red',
}

/** 手工建单一行明细的空白形态。 */
const EMPTY_ITEM = { sku_id: null as number | null, quantity: '', unit_price: '', specification: '' }

export default function OrderListPage({ initialTab = 'orders' }: { initialTab?: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const currentUser = useAuthStore((state) => state.user)
  const [selectedTab, setActiveKey] = useState(initialTab)
  const tab = TABS.find(({ itemKey }) => itemKey === selectedTab)
  const activeKey = tab && can(tab.permission) ? tab.itemKey : undefined
  /* 1024 宽内容区约 700px，销售订单八列合计 1114px，右侧「待回款 / 交期 /
     负责人」被裁、底部常驻横向滚动条。窄屏切紧凑档：交期并进订单号第二行、
     负责人并进客户第二行、「已回款 + 待回款」并成一列两行。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [receivablesPage, setReceivablesPage] = useState(1)
  const [paymentsPage, setPaymentsPage] = useState(1)
  const [createVisible, setCreateVisible] = useState(false)
  const createKey = useRef<string | null>(null)
  const [showItemIssues, setShowItemIssues] = useState(false)
  const [createForm, setCreateForm] = useState({
    customer_id: null as number | null,
    owner_id: null as number | null,
    delivery_date: '',
    payment_terms: '',
    remark: '',
    items: [{ ...EMPTY_ITEM }],
  })

  const ordersQuery = useQuery({
    queryKey: ['orders', { keyword, status, page, pageSize }],
    queryFn: () => listOrders({ keyword, status, page, page_size: pageSize }),
    enabled: activeKey === 'orders',
  })
  const receivablesQuery = useQuery({
    queryKey: ['receivables', receivablesPage],
    queryFn: () => listReceivables({ page: receivablesPage, page_size: 20 }),
    enabled: activeKey === 'receivables',
  })
  const paymentsQuery = useQuery({
    queryKey: ['payments', paymentsPage],
    queryFn: () => listPayments({ page: paymentsPage, page_size: 20 }),
    enabled: activeKey === 'payments',
  })
  const createSkusQuery = useQuery({
    queryKey: ['skus-for-pricing'],
    queryFn: listSkusForPricing,
    enabled: createVisible,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    void queryClient.invalidateQueries({ queryKey: ['orders'] })
    void queryClient.invalidateQueries({ queryKey: ['receivables'] })
    void queryClient.invalidateQueries({ queryKey: ['payments'] })
  }

  const confirmMutation = useMutation({
    mutationFn: (id: number) => confirmPayment(id, '财务核对通过'),
    onSuccess: () => {
      Toast.success('回款已确认。打开订单详情可核对各期已收金额。')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const rejectMutation = useMutation({
    mutationFn: (id: number) => rejectPayment(id, '金额或凭证有误'),
    onSuccess: () => {
      Toast.success('回款已驳回。核对金额和凭证后，可从订单对应期重新登记。')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ⚠️ 单价**不能留空**（2026-10-14 审查修）。原来判据是
  // `Number(item.unit_price) >= 0`，而 `EMPTY_ITEM.unit_price = ''` 且
  // **`Number('') === 0`**（`0 >= 0` 成立）—— 于是"选了 SKU、填了数量、
  // 忘了填单价"也满足"完整明细"，提交时 `Number('')` 变成 0，
  // 订单就以 **¥0 明细**建单（金额由后端按明细算，后端 `unit_price` 允许 `ge=0`），
  // 订单金额与后续应收计划一起错，而界面**一句提示都没有**。
  // 同一个坑项目里已经踩过并写明（`QuoteDetailPage.tsx` 的
  // 「不能写 `Number(x || 0)`：空串会被静默转成 0」），这里照那个口径拦。
  const itemIssue = (item: (typeof createForm.items)[number]): string | null => {
    if (!item.sku_id) return '未选 SKU'
    const quantity = item.quantity.trim()
    if (quantity === '') return '数量留空'
    try { requiredQuantityText(quantity) } catch (error) { return (error as Error).message }
    const price = item.unit_price.trim()
    // 空串必须单独拦：`Number('')` 是 0，不能靠数值判断发现"没填"
    if (price === '') return '单价留空（确属 0 元请显式填 0）'
    if (!/^\d+(?:\.\d+)?$/.test(price)) return '单价要是不小于 0 的数字'
    const [whole, fraction = ''] = price.split('.')
    if (whole.replace(/^0+/, '').length > 12 || fraction.replace(/0+$/, '').length > 4) return '单价整数最多 12 位，小数最多 4 位'
    return null
  }
  const enteredItems = createForm.items
    .map((item, index) => ({ item, line: index + 1 }))
    .filter(({ item }) => item.sku_id != null || item.quantity.trim() !== ''
      || item.unit_price.trim() !== '' || item.specification.trim() !== '')
  const itemIssues = enteredItems.flatMap(({ item, line }) => {
    const issue = itemIssue(item)
    return issue ? [`第 ${line} 行：${issue}`] : []
  })
  // 明细合计：填了 SKU / 数量 / 单价就实时算出来，别让人猜"到底多少钱"
  const coefficient = (text: string, places: number) => {
    const [whole, fraction = ''] = text.trim().replace(/^\+/, '').split('.')
    return BigInt((whole || '0') + fraction.padEnd(places, '0').slice(0, places))
  }
  // Match the server: round each positive line to cents before summing.
  const createTotal = itemIssues.length ? 0n : enteredItems.reduce((sum, { item }) =>
    sum + (coefficient(item.quantity, 3) * coefficient(item.unit_price, 4) + 50000n) / 100000n, 0n)
  const createMutation = useMutation({
    mutationFn: createOrder,
    onSuccess: (data) => {
      createKey.current = null
      Toast.success(`订单 ${data.order_no} 已创建。可在订单详情核对应收计划。`)
      setCreateVisible(false)
      setCreateForm({
        customer_id: null,
        owner_id: null,
        delivery_date: '',
        payment_terms: '',
        remark: '',
        items: [{ ...EMPTY_ITEM }],
      })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitCreate = () => {
    if (createMutation.isPending) return
    if (!createForm.customer_id) {
      Toast.warning('请选择客户')
      return
    }
    if (!enteredItems.length || itemIssues.length) {
      setShowItemIssues(true)
      return
    }
    createMutation.mutate({
      request_key: createKey.current ??= newRequestKey(),
      customer_id: createForm.customer_id,
      items: enteredItems.map(({ item }) => ({
        sku_id: item.sku_id!,
        quantity: item.quantity.trim(),
        unit_price: item.unit_price.trim(),
        specification: item.specification.trim() || undefined,
      })),
      owner_id: createForm.owner_id ?? undefined,
      delivery_date: createForm.delivery_date.trim() || undefined,
      payment_terms: createForm.payment_terms.trim() || undefined,
      remark: createForm.remark.trim() || undefined,
    })
  }

  const orderColumns = [
    {
      title: '订单号',
      dataIndex: 'order_no',
      width: compact ? 158 : 165,
      render: (text: string, record: Order) => (
        <div>
          <Link to={`/orders/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
            {text}
          </Link>
          {compact && (
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
              交期 {record.delivery_date ?? '-'}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      // 订单行里客户名是纯文本（不是链接）：按主标识加深，和订单号的蓝字呼应
      className: 'cell-strong',
      width: compact ? 190 : 215,
      render: (v: string | null, record: Order) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v ?? '-'}>
            {v ?? '-'}
          </div>
          {compact && (
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              负责人 {record.owner_name ?? '-'}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '订单金额',
      dataIndex: 'total_amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
      // 140 → 124：8 列合计本来正好 1130，但列表一长出现纵向滚动条，
      // 它占掉 15px，body 只剩 1115 → 横向滚动条又冒出来（2026-11-16 实测）。
      width: compact ? 108 : 124,
      render: (v: number, record: Order) =>
        formatMoney(toTwoDecimals(record.total_amount_exact ?? v), record.currency),
    },
    {
      title: compact ? '回款' : '已回款',
      dataIndex: 'received_amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
      width: compact ? 116 : 130,
      /* 这一列原来混着两种写法：有的行取到库里的精确字符串（¥2,100.00），
         有的行只有后端给的 number（¥0）—— 同一列里一半带两位小数、一半不带。
         没有精确字符串时把 number 补成两位小数再交给 formatMoney。 */
      render: (v: number, record: Order) => {
        const received = (
          <span style={{ color: record.unreceived_amount <= 0 ? 'var(--crm-success)' : undefined }}>
            {formatMoney(toTwoDecimals(record.received_amount_exact ?? v), record.currency)}
          </span>
        )
        if (!compact) return received
        /* 窄屏：上一行「已回」、下一行「待回」，省掉一列 */
        return (
          <div style={{ display: 'grid', gap: 2, justifyItems: 'end' }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>已回 {received}</span>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              待回 {formatMoney(toTwoDecimals(record.unreceived_amount_exact ?? record.unreceived_amount), record.currency)}
            </span>
          </div>
        )
      },
    },
    ...(compact
      ? []
      : [{
          title: '待回款',
          dataIndex: 'unreceived_amount',
          width: 125,
          /* 这一列原来漏了右对齐（同一行的「订单金额 / 已回款」都挂了
             .cell-right，只有它没有），¥1.00 和 ¥930.00 在格子中间各站各的，
             三列金额凑不成一条竖线（2026-11-19 实拍）。 */
          align: 'right' as const,
          className: 'cell-right',
          render: (v: number, record: Order) =>
            formatMoney(toTwoDecimals(record.unreceived_amount_exact ?? v), record.currency),
        }]),
    {
      title: (
        <span className="crm-help-label">
          履约状态
          <HelpTip forLabel="履约状态" text="订单从确认、备货、发货到完成的进度，由实际业务动作推进，不用手改。" />
        </span>
      ),
      dataIndex: 'status_label',
      width: compact ? 96 : 120,
      className: 'cell-money',
      render: (value: string, record: Order) => {
        const unpaid = record.unreceived_amount_exact ?? record.unreceived_amount
        const hasUnreceived = Number(unpaid) > 0
        const deliveryCompleted = record.status === 'completed'
        return (
          <div style={{ display: 'grid', gap: 2, justifyItems: 'start' }}>
            <StatusDot label={deliveryCompleted ? '交付已完成' : value} tone={ORDER_TONE[record.status] ?? 'grey'} />
            {deliveryCompleted && (
              <span
                style={{
                  fontSize: 11,
                  lineHeight: 1.4,
                  color: hasUnreceived ? 'var(--crm-text-2)' : 'var(--crm-text-3)',
                }}
              >
                {hasUnreceived ? `待收款 ${formatMoney(unpaid, record.currency)}` : '已收清'}
              </span>
            )}
          </div>
        )
      },
    },
    ...(compact
      ? []
      : [{
          title: '交期',
          dataIndex: 'delivery_date',
          width: 115,
          render: (v: string | null) => v ?? '-',
        }]),
    ...(compact
      ? []
      : [{ title: '负责人', dataIndex: 'owner_name', width: 95, render: (v: string | null) => v ?? '-' }]),
  ]

  const receivableColumns = [
    { title: '订单号', dataIndex: 'order_no', width: compact ? 154 : 170,
      render: (v: string | null, record: Receivable) => v && can('order:view')
        ? <Link to={`/orders/${record.order_id}`}>{v}</Link> : v ?? '-' },
    {
      title: '节点',
      dataIndex: 'plan_name',
      width: compact ? 130 : 120,
      render: (v: string | null) => (
        <span title={v ?? ''} style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {v ?? '-'}
        </span>
      ),
    },
    { title: '应收日期', dataIndex: 'due_date', width: compact ? 112 : 130 },
    {
      title: '应收金额',
      dataIndex: 'amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
      width: compact ? 100 : 130,
      /* 同一行里「已收/未收」走的是库里的精确十进制字符串（900.00），
         这里原来传的是 number（900）—— 一行里一半带两位小数、一半不带，
         看着像两个系统拼起来的。统一成精确值。 */
      render: (v: number, record: Receivable) =>
        formatMoney(toTwoDecimals(record.amount_exact ?? v), record.currency),
    },
    {
      title: compact ? '已收 / 未收' : '已收',
      dataIndex: 'received_amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
      width: compact ? 110 : 130,
      render: (v: number, record: Receivable) => {
        /* 精确字符串可能是 "0"（不带小数），formatMoney 会原样印成「¥0」——
            同一张表里别的格子是「¥0.00」，一半带小数一半不带（2026-11-21 巡检）。
            先过 toTwoDecimals 补到两位，全表一个口径。 */
        const received = formatMoney(toTwoDecimals(record.received_amount_exact ?? v), record.currency)
        if (!compact) return received
        /* 窄屏：上一行已收、下一行未收，两张钱合并一列 */
        return (
          <div style={{ display: 'grid', gap: 2, justifyItems: 'end' }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>已收 {received}</span>
            <span
              style={{
                fontSize: 12,
                color: record.status === 'overdue' ? '#f53f3f' : 'var(--crm-text-3)',
                fontWeight: record.status === 'overdue' ? 600 : undefined,
              }}
            >
              未收 {formatMoney(toTwoDecimals(record.remaining_amount_exact ?? record.remaining_amount), record.currency)}
            </span>
          </div>
        )
      },
    },
    ...(compact
      ? []
      : [{
          title: '未收',
          dataIndex: 'remaining_amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
          width: 130,
          /* 逾期行把"未收"标红加粗：这是这一行唯一还没回来的钱，
             原来和已回款行的未收 ¥0.00 长得一样，扫一遍看不出哪笔真欠着。 */
          render: (v: number, record: Receivable) => {
            const overdue = record.status === 'overdue'
            return (
              <span
                style={
                  overdue
                    ? { color: '#f53f3f', fontWeight: 600 }
                    : undefined
                }
              >
                {formatMoney(record.remaining_amount_exact ?? v, record.currency)}
              </span>
            )
          },
        }]),
    {
      title: '状态',
      dataIndex: 'status_label',
      width: compact ? 92 : 110,
      render: (value: string, record: Receivable) => (
        <StatusDot label={value} tone={PLAN_TONE[record.status] ?? 'grey'} bold={record.status === 'overdue'} />
      ),
    },
  ]

  const paymentColumns = [
    {
      title: '订单号',
      dataIndex: 'order_no',
      width: compact ? 150 : 160,
      render: (v: string | null, record: Payment) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v ?? '-'}>
            {v && can('order:view') ? <Link to={`/orders/${record.order_id}`}>{v}</Link> : v ?? '-'}
          </div>
          {compact && (
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {record.plan_name ?? '-'}
            </div>
          )}
        </div>
      ),
    },
    ...(compact
      ? []
      : [{ title: '应收节点', dataIndex: 'plan_name', width: 120, render: (v: string | null) => v ?? '-' }]),
    { title: '收款日期', dataIndex: 'received_date', width: compact ? 108 : 118 },
    {
      title: '金额',
      dataIndex: 'received_amount',
      /* 金额右对齐：位数不同的钱左对齐时右边界参差，一眼读不出量级。 */
      align: 'right' as const,
      /* 全局 CSS 把每个单元格居中，Semi 的 align 不写 inline style，
         所以金额仍被居中；补一个 class 由下面 CSS 用 !important 兜住。 */
      className: 'cell-right',
      width: compact ? 104 : 118,
      render: (v: number, record: Payment) => formatMoney(toTwoDecimals(record.received_amount_exact ?? v), record.currency),
    },
    ...(compact
      ? []
      : [{ title: '方式', dataIndex: 'payment_method', width: 100, render: (v: string | null) => v ?? '-' }]),
    {
      title: '回款凭证',
      width: compact ? 120 : 170,
      render: (_: unknown, record: Payment) => (
        <div style={{ display: 'grid', gap: 2, justifyItems: 'end', minWidth: 0 }}>
          <PaymentVoucherControl
            payment={record}
            onChanged={() => void queryClient.invalidateQueries({ queryKey: ['payments'] })}
          />
          {compact && (
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{record.payment_method ?? '-'}</span>
          )}
        </div>
      ),
    },
    {
      title: compact ? '状态 / 确认人' : '状态',
      dataIndex: 'status_label',
      width: compact ? 96 : 110,
      render: (value: string, record: Payment) => {
        const dot = (
          <StatusDot label={value} tone={value === '已确认' ? 'green' : value === '已驳回' ? 'red' : value === '已作废' ? 'grey' : 'orange'} />
        )
        if (!compact) return dot
        return (
          <div style={{ display: 'grid', gap: 2, justifyItems: 'end' }}>
            {dot}
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {record.status === 'voided'
                ? `作废人 ${record.voided_by_name ?? '-'}`
                : `确认人 ${record.confirmed_by_name ?? '-'}`}
            </span>
          </div>
        )
      },
    },
    ...(compact
      ? []
      : [{
          title: '确认人',
          dataIndex: 'confirmed_by_name',
          width: 100,
          render: (v: string | null) => v ?? '-',
        }]),
    {
      title: '操作',
      width: compact ? 100 : 130,
      render: (_: unknown, record: Payment) =>
        record.status === 'pending' && can('payment:manage') ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {/* 回款确认/驳回是财务动作、点一下立即生效，必须二次确认
                （主人 2026-10-06：表格里的动作「点一下就转换成功了，有点儿戏」）。 */}
            <Popconfirm
              title="确认收到这笔回款？"
              content={`请核对到账日期、凭证和金额 ${formatMoney(record.received_amount_exact ?? record.received_amount, record.currency)}。确认后会计入实收。`}
              onConfirm={() => confirmMutation.mutate(record.id)}
            >
              <Button size="small" theme="borderless">确认</Button>
            </Popconfirm>
            <Popconfirm
              title="驳回这笔回款？"
              content="驳回后，这笔记录不计入实收。请告知登记人核对金额和凭证，再从订单对应期重新登记。"
              onConfirm={() => rejectMutation.mutate(record.id)}
            >
              <Button size="small" theme="borderless" type="danger">驳回</Button>
            </Popconfirm>
          </div>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      {/* 这个组件同时挂在 /orders 与 /receivables 两条路由上（侧栏是两个独立
          菜单项）。标题跟着入口走 —— 以前从「回款中心」进来，页头还写「订单
          中心」，页签又写「应收计划」，三个名字打架（2026-11-16 巡检发现）。 */}
      <PageHeader
        title={initialTab === 'receivables' ? '回款中心' : '订单中心'}
        extra={
          initialTab === 'receivables' ? undefined : (
            <Link to="/order-drafts"><Button>订单草稿</Button></Link>
          )
        }
        subtitle={
          initialTab === 'receivables'
            ? '按应收计划看每笔订单该收多少、已收多少、逾期多少；回款由财务确认'
            : '成交报价转成订单后，履约由 ERP/MES 负责、回款由财务确认；CRM 只保留关键节点'
        }
      />

      <SectionCard>
        <Tabs type="line" activeKey={selectedTab} onChange={setActiveKey} tabList={TABS} />

        <div style={{ marginTop: 16 }}>
          {tab && !activeKey && (
            <p className="workflow-guide">当前账号没有查看「{tab.tab}」的权限。如需查看，请联系管理员。</p>
          )}
          {activeKey === 'orders' && (
            <>
              <div className="toolbar">
                <Input
                  placeholder="搜索订单号"
                  value={keywordInput}
                  onChange={setKeywordInput}
                  onEnterPress={() => {
                    setKeyword(keywordInput.trim())
                    setPage(1)
                  }}
                  style={{ width: 220 }}
                  showClear
                />
                <Select
                  placeholder="履约状态"
                  value={status}
                  onChange={(value) => {
                    setStatus(value as string | undefined)
                    setPage(1)
                  }}
                  optionList={[
                    { value: 'pending', label: '待生产' },
                    { value: 'in_production', label: '生产中' },
                    { value: 'shipped', label: '已发货' },
                    { value: 'delivered', label: '已签收' },
                    { value: 'completed', label: '交付已完成' },
                    { value: 'cancelled', label: '已取消' },
                  ]}
                  style={{ width: 150 }}
                  showClear
                />
                <Button
                  type="tertiary"
                  theme="borderless"
                  onClick={() => {
                  setKeyword(keywordInput.trim())
                  setPage(1)
                  }}
                >
                  查询
                </Button>
                {/* 手工建单不需要"客户已接受报价"却计入业绩，口径是**只有主管能用**：
                    后端按 order:assign 把守，按钮跟着同一权限点走（不能只靠前端隐藏，
                    但也不该让业务员点到一个必然 403 的按钮）。 */}
                {can('order:assign') && (
                  <Button type="primary" theme="outline" onClick={() => {
                    setShowItemIssues(false)
                    setCreateVisible(true)
                  }}>
                    手工建单
                  </Button>
                )}
              </div>
              <Table<Order>
                className="orders-table"
                columns={orderColumns}
                dataSource={ordersQuery.data?.items ?? []}
                loading={ordersQuery.isLoading}
                rowKey="id"
                empty={emptyTextNode(
                  ordersQuery,
                  '还没有订单',
                  '成交报价转成订单后，会出现在这里。',
                  <Button size="small" theme="outline" onClick={() => navigate('/quotes')}>去报价中心</Button>,
                )}
                pagination={{
                  currentPage: page,
                  pageSize,
                  total: ordersQuery.data?.total ?? 0,
                  showSizeChanger: true,
                  onPageChange: (next: number) => setPage(next),
                  onPageSizeChange: (size: number) => {
                    setPageSize(size)
                    setPage(1)
                  },
                }}
              />
            </>
          )}

          {activeKey === 'receivables' && (
            <>
            <p className="workflow-guide">
              应收计划列的是约定要收的款。客户实际打款后，点订单号进入订单详情，在「应收计划」找到对应期登记。
            </p>
            <Table<Receivable>
              className="receivables-table"
              columns={receivableColumns}
              dataSource={receivablesQuery.data?.items ?? []}
              loading={receivablesQuery.isLoading}
              rowKey="id"
              pagination={{
                currentPage: receivablesPage,
                pageSize: 20,
                total: receivablesQuery.data?.total ?? 0,
                onPageChange: setReceivablesPage,
              }}
              empty={emptyTextNode(receivablesQuery, '还没有应收计划', '在订单详情查看应收计划，或联系财务生成。')}
            />
            </>
          )}

          {activeKey === 'payments' && (
            <>
            <p className="workflow-guide">{can('payment:manage')
              ? '先核对银行到账和凭证，再处理状态为「待确认」的回款。'
              : '财务确认后，回款才计入已收。可查看每笔的处理状态。'}
              {can('order:view') && <>点订单号可回到订单详情；也可以到<Link to="/orders" style={{ margin: '0 4px' }}>订单中心</Link>按订单查。</>}
            </p>
            <Table<Payment>
              className="payments-table"
              columns={paymentColumns}
              dataSource={paymentsQuery.data?.items ?? []}
              loading={paymentsQuery.isLoading}
              rowKey="id"
              pagination={{
                currentPage: paymentsPage,
                pageSize: 20,
                total: paymentsQuery.data?.total ?? 0,
                onPageChange: setPaymentsPage,
              }}
              empty={emptyTextNode(paymentsQuery, '还没有回款记录', '已经到账的款项可到订单对应的应收期登记。')}
            />
            </>
          )}
        </div>
      </SectionCard>

      <Modal
        title="手工建单（线下签约 / 补录历史单）"
        visible={createVisible}
        onCancel={() => { if (!createMutation.isPending) setCreateVisible(false) }}
        onOk={submitCreate}
        confirmLoading={createMutation.isPending}
        okText="创建订单"
        width={680}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 12 }}>
            <div>
              <FormLabel required>客户</FormLabel>
              <RecordSearchSelect
                key={String(createVisible)}
                scope="order-customer"
                enabled={createVisible}
                placeholder="按客户名称或编号搜索"
                value={createForm.customer_id ?? undefined}
                list={listCustomers}
                lookup={getCustomer}
                labelOf={(customer) => customer.name}
                onChange={(customer) => setCreateForm({ ...createForm, customer_id: customer?.id ?? null })}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>负责人（默认自己）</div>
              <UserSearchSelect
                queryScope="order-owner"
                enabled={createVisible}
                placeholder="按姓名或登录账号搜索"
                value={createForm.owner_id ?? currentUser?.id}
                fallbackLabel={createForm.owner_id == null ? currentUser?.name : undefined}
                onChange={(value) => setCreateForm({ ...createForm, owner_id: value ?? null })}
                style={{ width: '100%' }}
              />
            </div>
          </div>

          <div>
            <FormLabel required style={{ fontWeight: 600 }}>订单明细</FormLabel>
            <div style={{ display: 'grid', gap: 8 }}>
              {createForm.items.map((item, index) => (
                <div key={index} className="order-item-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) 90px 110px minmax(0, 1.2fr) auto', gap: 8 }}>
                  <Select
                    placeholder="选择 SKU"
                    value={item.sku_id ?? undefined}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, sku_id: value as number }
                      setCreateForm({ ...createForm, items })
                    }}
                    optionList={(createSkusQuery.data ?? []).map((sku) => ({
                      value: sku.id,
                      // 带上 SKU 名称：同一产品下多个 SKU 光看编码 + 规格分不清
                      label: skuLabel(sku),
                    }))}
                    filter={optionMatcher}
                    style={{ width: '100%' }}
                  />
                  <Input
                    placeholder="数量"
                    value={item.quantity}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, quantity: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  <Input
                    placeholder="单价"
                    value={item.unit_price}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, unit_price: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  <Input
                    placeholder="规格（可空）"
                    value={item.specification}
                    onChange={(value) => {
                      const items = [...createForm.items]
                      items[index] = { ...item, specification: value }
                      setCreateForm({ ...createForm, items })
                    }}
                  />
                  {createForm.items.length === 1 ? (
                    /* 只剩一行时删不了，把原因直接写出来 —— 灰按钮不给理由，
                       点下去也没反应（30-前端约定）。 */
                    <span
                      style={{
                        alignSelf: 'center',
                        fontSize: 12,
                        color: 'var(--crm-text-3)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      仅一行
                    </span>
                  ) : (
                    <Button
                      type="danger"
                      onClick={() =>
                        setCreateForm({
                          ...createForm,
                          items: createForm.items.filter((_, i) => i !== index),
                        })
                      }
                    >
                      删
                    </Button>
                  )}
                </div>
              ))}
            </div>
            <Button
              style={{ marginTop: 8 }}
              onClick={() => setCreateForm({ ...createForm, items: [...createForm.items, { ...EMPTY_ITEM }] })}
            >
              加一行
            </Button>
            {showItemIssues && (!enteredItems.length || itemIssues.length > 0) && (
              <div role="alert" style={{ marginTop: 8, color: 'var(--crm-error)' }}>
                {enteredItems.length ? `请补齐商品明细：${itemIssues.join('；')}` : '请至少填写一行商品（SKU、数量、单价）'}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 12 }}>
            <div>
              <div style={{ marginBottom: 4 }}>交期（可空）</div>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="选择交期"
                value={createForm.delivery_date ? new Date(createForm.delivery_date) : undefined}
                onChange={(_, dateStr) =>
                  setCreateForm({ ...createForm, delivery_date: (dateStr as string) || '' })
                }
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>付款条件（可空）</div>
              <Input
                placeholder="款到发货"
                value={createForm.payment_terms}
                onChange={(value) => setCreateForm({ ...createForm, payment_terms: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注（可空）</div>
            <Input
              value={createForm.remark}
              onChange={(value) => setCreateForm({ ...createForm, remark: value })}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
              金额由明细自动算出，不需要手填；创建后可在订单详情里生成应收计划。
            </span>
            {createTotal > 0n && (
              <span style={{ marginLeft: 'auto', fontSize: 13 }}>
                明细合计 <strong>{formatMoney(paymentMoney(createTotal), 'CNY')}</strong>
                <span style={{ color: 'var(--crm-text-3)', marginLeft: 6 }}>
                  {enteredItems.length} 行
                </span>
              </span>
            )}
          </div>
        </div>
      </Modal>
    </div>
  )
}
