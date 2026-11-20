import { skuLabel } from '../../shared/sku-label'
import PageLoading from '../../shared/components/PageLoading'
import { formatBeijingTime } from '../../shared/beijing-time'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  DatePicker,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tabs,
  Tag,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import { IconApps, IconFile, IconHistogram } from '@douyinfe/semi-icons'
import { getOpportunityTimeline, listFollowups, type FollowUp } from '../../shared/api/followup'
import { listQuotes } from '../../shared/api/quote'
import { listOrders } from '../../shared/api/order'
import {
  changeStage,
  createItem,
  deleteItem,
  getOpportunity,
  listItems,
  listLossReasons,
  listStages,
  loseOpportunity,
  confirmWin,
  type OpportunityItem,
} from '../../shared/api/opportunity'
import { listSkus } from '../../shared/api/product'
import { completeTask, createTask, listTasks, type Task } from '../../shared/api/task'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import { calendarDay } from '../../shared/calendar-day'
import GuardedButton from '../../shared/components/GuardedButton'
import ReceivablePlanPicker, {
  defaultPlanRows,
  planBlockedReasons,
  toPlanPayload,
  type ReceivablePlanRow,
} from '../../shared/components/ReceivablePlanPicker'
import SectionCard from '../../shared/components/SectionCard'
import EmptyState from '../../shared/components/EmptyState'
import AgentInsight from '../../shared/components/AgentInsight'
import {
  agentOpportunityAnalysis,
  agentProductRecommendation,
  agentQuoteDraft,
  type AnalysisEnvelope,
} from '../../shared/api/agent'
import DetailField from '../common/DetailField'
import FollowUpModal from '../common/FollowUpModal'
import FollowUpAttachmentsButton from '../common/FollowUpAttachmentsButton'
import Timeline from '../common/Timeline'
import AttachmentPanel from '../common/AttachmentPanel'
import DecisionMakerCard from '../common/DecisionMakerCard'
import OpportunityRecords from './OpportunityRecords'
import type { TagTone } from '../../shared/types'
import FormLabel from '../../shared/components/FormLabel'
import { optionMatcher } from '../../shared/components/optionMatch'
import { formatMoney, toTwoDecimals } from '../../shared/components/money'

const TABS = [
  { tab: '概览', itemKey: 'overview' },
  { tab: '需求商品', itemKey: 'items' },
  { tab: '关联单据', itemKey: 'records' },
  { tab: '跟进', itemKey: 'followups' },
  { tab: '任务', itemKey: 'tasks' },
  { tab: '文件', itemKey: 'files' },
  { tab: '时间线', itemKey: 'timeline' },
]

const RISK_LABEL: Record<string, string> = { high: '高风险', medium: '中风险', low: '低风险' }
const PRIORITY_COLOR: Record<string, TagTone> = { high: 'red', normal: 'blue', low: 'grey' }

export default function OpportunityDetailPage() {
  const params = useParams()
  const opportunityId = Number(params.id)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { can } = usePermissions()
  const canManage = can('opportunity:manage')

  const [activeKey, setActiveKey] = useState('overview')
  const [followupVisible, setFollowupVisible] = useState(false)

  // AI 分析（API §37 专用接口，需 agent:use）
  const [aiEnvelope, setAiEnvelope] = useState<AnalysisEnvelope | null>(null)
  // 记一下最近跑的是哪一项：三段各自只在自己的结果区里显示，避免
  // "点了产品推荐，结果跑到商机分析下面去"这种张冠李戴。
  const [aiKind, setAiKind] = useState<'analysis' | 'recommend' | 'draft' | null>(null)
  const aiMutation = useMutation({
    mutationFn: (kind: 'analysis' | 'recommend' | 'draft') => {
      if (kind === 'analysis') return agentOpportunityAnalysis({ opportunity_id: opportunityId })
      if (kind === 'recommend')
        return agentProductRecommendation({ opportunity_id: opportunityId })
      return agentQuoteDraft({ opportunity_id: opportunityId })
    },
    onSuccess: (data) => setAiEnvelope(data),
    onError: (error: Error) => Toast.error(error.message),
  })

  const [stageVisible, setStageVisible] = useState(false)
  const [targetStage, setTargetStage] = useState<number | null>(null)
  const [stageRemark, setStageRemark] = useState('')

  const [loseVisible, setLoseVisible] = useState(false)
  const [lossReasonId, setLossReasonId] = useState<number | null>(null)
  const [lossRemark, setLossRemark] = useState('')

  const [itemVisible, setItemVisible] = useState(false)
  const [itemForm, setItemForm] = useState({
    sku_id: null as number | null,
    quantity: '',
    target_price: '',
    specification: '',
    color: '',
    package_requirement: '',
    delivery_date: null as Date | null,
    destination: '',
    remark: '',
  })

  const [taskVisible, setTaskVisible] = useState(false)
  const [taskForm, setTaskForm] = useState({
    title: '',
    priority: 'normal',
    due_at: null as Date | null,
  })

  const opportunityQuery = useQuery({
    queryKey: ['opportunity', opportunityId],
    queryFn: () => getOpportunity(opportunityId),
    enabled: Number.isFinite(opportunityId),
  })
  // 页签标题用商机名，避免多个「商机详情」分不清
  useTabLabel(opportunityQuery.data?.title)
  const itemsQuery = useQuery({
    queryKey: ['opportunity-items', opportunityId],
    queryFn: () => listItems(opportunityId),
    enabled: Number.isFinite(opportunityId),
  })
  const followupsQuery = useQuery({
    queryKey: ['followups', { opportunity_id: opportunityId }],
    queryFn: () => listFollowups({ opportunity_id: opportunityId, page_size: 100 }),
    enabled: Number.isFinite(opportunityId),
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks', { opportunity_id: opportunityId }],
    queryFn: () => listTasks({ opportunity_id: opportunityId, page_size: 100 }),
    enabled: Number.isFinite(opportunityId),
  })
  // 概览「商机全景」要报"这单开了几张报价、出了几张订单"。
  // 这两个数只有 total 有用，page_size 压到 1，别把整张表拉回来。
  // 没有该模块权限（quote:view / order:view）就不发请求，格子显示「—」。
  const quoteCountQuery = useQuery({
    queryKey: ['opportunity-quote-count', opportunityId],
    queryFn: () => listQuotes({ opportunity_id: opportunityId, page_size: 1 }),
    enabled: Number.isFinite(opportunityId) && activeKey === 'overview' && can('quote:view'),
  })
  const orderCountQuery = useQuery({
    queryKey: ['opportunity-order-count', opportunityId],
    queryFn: () => listOrders({ opportunity_id: opportunityId, page_size: 1 }),
    enabled: Number.isFinite(opportunityId) && activeKey === 'overview' && can('order:view'),
  })
  const timelineQuery = useQuery({
    queryKey: ['timeline', 'opportunity', opportunityId],
    queryFn: () => getOpportunityTimeline(opportunityId),
    enabled: Number.isFinite(opportunityId) && activeKey === 'timeline',
  })
  const stagesQuery = useQuery({ queryKey: ['stages'], queryFn: listStages })
  const lossReasonsQuery = useQuery({
    queryKey: ['loss-reasons'],
    queryFn: listLossReasons,
    enabled: loseVisible,
  })
  const skusQuery = useQuery({
    queryKey: ['skus-for-select'],
    queryFn: () => listSkus({ page: 1, page_size: 100 }),
    enabled: itemVisible,
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
    void queryClient.invalidateQueries({ queryKey: ['opportunity-items', opportunityId] })
    void queryClient.invalidateQueries({ queryKey: ['followups'] })
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    void queryClient.invalidateQueries({ queryKey: ['timeline'] })
    void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
    void queryClient.invalidateQueries({ queryKey: ['funnel'] })
  }

  const stageMutation = useMutation({
    mutationFn: () => changeStage(opportunityId, { stage_id: targetStage!, remark: stageRemark || undefined }),
    onSuccess: (result) => {
      Toast.success(`已推进到「${result.stage_name}」`)
      setStageVisible(false)
      setStageRemark('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 确认成交并生成订单（方案 §5 / A13）：替代"标记成交后再去报价页转单"的两步操作
  /**
   * 成交并建单弹窗（2026-10-13 改动三）：成交那一刻就能选收款方式。
   *
   * ⚠️ 这里**不给金额预览**：成交转的是"最新已发送/已接受的报价版本"（后端挑），
   * 前端此刻不知道是哪一份、金额多少；拿「预计金额」凑会误导用户。
   * 所以金额那部分交给转单时按报价版本算（页面上也如实说明了）。
   */
  const [confirmWinVisible, setConfirmWinVisible] = useState(false)
  const [planMode, setPlanMode] = useState<'full' | 'installments'>('full')
  const [planRows, setPlanRows] = useState<ReceivablePlanRow[]>(defaultPlanRows)
  const planBlocked = planBlockedReasons(planMode, planRows)
  // 「能不能点」与「为什么不能点」必须是**同一个判据**（`planBlocked` 非空即被挡）。
  // 原来 `!isPlanValid(...)` 在这里重复算了三次，三次一旦不同步就会出现
  // 「按钮能点但右边写着不能点」这种自相矛盾的界面。
  const planNotReady = planBlocked.length > 0

  const confirmWinMutation = useMutation({
    mutationFn: () => {
      // 模块⑤：交期自动带入——取需求明细里最早的非空交期，
      // 销售填过一遍的交期不要求再手填（后端也有同样回退兜底）
      const dates = (itemsQuery.data ?? [])
        .map((item) => item.delivery_date)
        .filter((d): d is string => Boolean(d))
        .sort()
      return confirmWin(opportunityId, {
        delivery_date: dates[0] ?? null,
        remark: '在商机详情页确认成交',
        receivable_plan: toPlanPayload(planMode, planRows) ?? undefined,
      })
    },
    onSuccess: (data) => {
      Toast.success(
        data.already_ordered
          ? `该成交此前已建单：${data.order_no}`
          : `已确认成交，销售订单 ${data.order_no} 已生成`,
      )
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const loseMutation = useMutation({
    mutationFn: () => loseOpportunity(opportunityId, { loss_reason_id: lossReasonId!, remark: lossRemark || undefined }),
    onSuccess: () => {
      Toast.success('商机已失单')
      setLoseVisible(false)
      setLossRemark('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const itemMutation = useMutation({
    mutationFn: () =>
      createItem(opportunityId, {
        sku_id: itemForm.sku_id,
        quantity: itemForm.quantity ? Number(itemForm.quantity) : 1,
        target_price: itemForm.target_price ? Number(itemForm.target_price) : null,
        specification: itemForm.specification || null,
        color: itemForm.color || null,
        package_requirement: itemForm.package_requirement || null,
        delivery_date: itemForm.delivery_date
          ? calendarDay(itemForm.delivery_date)
          : null,
        destination: itemForm.destination || null,
        remark: itemForm.remark || null,
      }),
    onSuccess: () => {
      Toast.success('需求明细已添加')
      setItemVisible(false)
      setItemForm({
        sku_id: null,
        quantity: '',
        target_price: '',
        specification: '',
        color: '',
        package_requirement: '',
        delivery_date: null,
        destination: '',
        remark: '',
      })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteItemMutation = useMutation({
    mutationFn: (itemId: number) => deleteItem(itemId),
    onSuccess: () => {
      Toast.success('已删除')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const taskMutation = useMutation({
    mutationFn: () =>
      createTask({
        title: taskForm.title,
        task_type: 'opportunity',
        opportunity_id: opportunityId,
        customer_id: opportunityQuery.data?.customer_id ?? null,
        due_at: taskForm.due_at ? taskForm.due_at.toISOString() : null,
        priority: taskForm.priority,
      }),
    onSuccess: () => {
      Toast.success('任务已创建')
      setTaskVisible(false)
      setTaskForm({ title: '', priority: 'normal', due_at: null })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const completeTaskMutation = useMutation({
    mutationFn: (taskId: number) => completeTask(taskId),
    onSuccess: () => {
      Toast.success('任务已完成')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const opportunity = opportunityQuery.data
  if (opportunityQuery.isLoading) return <PageLoading />
  if (!opportunity) {
    return (
      <div className="page-container">
        <EmptyState
          title="商机不存在或无权查看"
          hint="可能链接里的编号有误，或这条记录属于你数据范围之外的人；回商机列表重新选择即可。"
        />
        <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 24 }}>
          <Button type="primary" theme="solid" onClick={() => navigate('/opportunities')}>返回商机列表</Button>
        </div>
      </div>
    )
  }

  const itemColumns = [
    {
      title: '产品名称',
      dataIndex: 'sku_code',
      width: 170,
      // 编码 + 名称（口径与报价/订单明细一致）。接口已返回 `sku_name`。
      render: (v: string | null, record: OpportunityItem) => (
        <div>
          <div>{record.sku_name || v || '未命名产品'}</div>
          {v && record.sku_name && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{v}</div>
          )}
        </div>
      ),
    },
    { title: '规格', dataIndex: 'specification', width: 200, render: (v: string | null) => v ?? '-' },
    { title: '颜色', dataIndex: 'color', width: 90, render: (v: string | null) => v ?? '-' },
    {
      title: '数量',
      dataIndex: 'quantity',
      width: 110,
      render: (v: number, record: OpportunityItem) => `${v} ${record.unit ?? ''}`,
    },
    {
      title: '目标价',
      dataIndex: 'target_price',
      width: 110,
      render: (v: number | null) => (v ? `¥${v}` : '-'),
    },
    { title: '交期', dataIndex: 'delivery_date', width: 120, render: (v: string | null) => v ?? '-' },
    { title: '目的地', dataIndex: 'destination', width: 120, render: (v: string | null) => v ?? '-' },
    { title: '客户要求', dataIndex: 'package_requirement', render: (v: string | null) => v ?? '-' },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, record: OpportunityItem) =>
        canManage ? (
          <Popconfirm title="删除这条需求？" onConfirm={() => deleteItemMutation.mutate(record.id)}>
            <a style={{ color: 'var(--crm-error)' }}>删除</a>
          </Popconfirm>
        ) : (
          '-'
        ),
    },
  ]

  /* 商机详情几个"空"页签原来各说各的：需求商品/跟进只有一行小字，任务却摆了个
     大图标空态（350px 高），同一排页签点来点去高度来回跳。统一成"一句主文 +
     一句怎么做"，既一致又不空。 */
  const emptyLine = (title: string, hint: string) => (
    <div style={{ padding: '16px 0', textAlign: 'center' }}>
      <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>{title}</div>
      <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-3)' }}>{hint}</div>
    </div>
  )

  const taskColumns = [
    { title: '任务', dataIndex: 'title' },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 90,
      render: (value: string, record: Task) => (
        <Tag color={PRIORITY_COLOR[value] ?? 'grey'}>{record.priority_label}</Tag>
      ),
    },
    {
      title: '截止时间',
      dataIndex: 'due_at',
      width: 170,
      render: (value: string | null, record: Task) =>
        value ? (
          <span style={{ color: record.overdue ? 'var(--crm-error)' : undefined }}>
            {formatBeijingTime(value)}
            {record.overdue ? '（已逾期）' : ''}
          </span>
        ) : (
          '-'
        ),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string) => (value === '已完成' ? <Tag color="green">{value}</Tag> : value),
    },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record: Task) =>
        record.status !== 'done' ? (
          <Popconfirm
            title="确认完成这条任务？"
            content="完成后任务会从未完成列表中移除。"
            onConfirm={() => completeTaskMutation.mutate(record.id)}
          >
            <a style={{ color: 'var(--crm-primary)' }}>完成</a>
          </Popconfirm>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      {/* 与客户详情页同排布：标题 + 标签一行，关键信息行在标题下方左对齐 */}
      <DetailHeader
        title={opportunity.title}
        tags={
          <>
            <Tag color={opportunity.status === 'win' ? 'green' : opportunity.status === 'loss' ? 'grey' : 'blue'}>
              {opportunity.stage_name}
            </Tag>
            {opportunity.risk_level && (
              <Tag color={opportunity.risk_level === 'high' ? 'red' : 'orange'}>
                {RISK_LABEL[opportunity.risk_level] ?? opportunity.risk_level}
              </Tag>
            )}
          </>
        }
        metaFull
        meta={
          <>
            <span>
              客户：
              <Link to={`/customers/${opportunity.customer_id}`} style={{ color: 'var(--crm-primary)' }}>
                {opportunity.customer_name}
              </Link>
            </span>
            <span>负责人：{opportunity.owner_name ?? '-'}</span>
            <span>
              预计金额：
              {opportunity.expected_amount
                ? formatMoney(toTwoDecimals(opportunity.expected_amount), 'CNY')
                : '-'}
            </span>
            <span>预计成交：{opportunity.expected_close_date ?? '-'}</span>
            <span>需求条数：{opportunity.item_count}</span>
          </>
        }
        extra={
          <>
            <Button type="tertiary" onClick={() => setFollowupVisible(true)}>
              记录跟进
            </Button>
            <Button
              type="tertiary"
              onClick={() => {
                // 深链预填核价页：带上客户；有需求明细时带首条 SKU 与数量
                const query = new URLSearchParams({ customer_id: String(opportunity.customer_id) })
                const firstItem = (itemsQuery.data ?? [])[0]
                if (firstItem?.sku_id) {
                  query.set('sku_id', String(firstItem.sku_id))
                  query.set('quantity', String(firstItem.quantity ?? 1000))
                }
                navigate(`/pricing?${query.toString()}`)
              }}
            >
              去核价
            </Button>
            {canManage && (
              <>
                <Button
                  type="tertiary"
                  onClick={() => {
                    setTargetStage(null)
                    setStageVisible(true)
                  }}
                >
                  推进阶段
                </Button>
                <Button type="tertiary" onClick={() => setTaskVisible(true)}>
                  新建任务
                </Button>
                {opportunity.status === 'open' && (
                  <>
                    <Button type="primary"
                      theme="solid"
                      loading={confirmWinMutation.isPending}
                      onClick={() => {
                        setPlanMode('full')
                        setPlanRows(defaultPlanRows())
                        setConfirmWinVisible(true)
                      }}
                    >
                      确认成交并建单
                    </Button>
                    <Button type="danger" theme="borderless" onClick={() => setLoseVisible(true)}>
                      标记失单
                    </Button>
                  </>
                )}
              </>
            )}
          </>
        }
      />

      <SectionCard>
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />

        <div style={{ marginTop: 16 }}>
          {activeKey === 'overview' && (
            <div className="detail-split" style={{ gap: 24 }}>
              <div className="split-main" style={{ minWidth: 0 }}>
                {/* 2026-11-20：左栏原来只有八个小字段贴在顶部，下面空一大截，
                    右栏「决策卡 + 三个按钮」却一路垂到底 —— 主人原话"左上角高、右下角低"，
                    后来改成居中又说"上下都空空的"。根子是左右两栏高度和密度都不匹配。
                    现在左栏补一行和右栏同规格的标题（商机概览 / 共 N 项），
                    字段从 4 列 2 行改成 3 列 3 行：版面长到跟右栏差不多高，
                    中间不再留大片空白，两栏顶端也齐在同一行。 */}
                <div className="toolbar" style={{ marginBottom: 8 }}>
                  <div style={{ fontWeight: 600 }}>商机概览</div>
                  <div style={{ flex: 1 }} />
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                    共 {opportunity.status === 'loss' ? 9 : 8} 项
                  </span>
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                    gap: '18px 24px',
                  }}
                >
                  <DetailField label="客户" value={opportunity.customer_name} />
                  <DetailField label="当前阶段" value={opportunity.stage_name} />
                  <DetailField
                    label="预计金额"
                    value={opportunity.expected_amount ? formatMoney(toTwoDecimals(opportunity.expected_amount), 'CNY') : '-'}
                  />
                  <DetailField label="预计成交日期" value={opportunity.expected_close_date ?? '-'} />
                  <DetailField label="竞争对手" value={opportunity.competitor ?? '-'} />
                  <DetailField label="下一步动作" value={opportunity.next_action ?? '-'} />
                  <DetailField label="来源" value={opportunity.source ?? '-'} />
                  <DetailField label="创建时间" value={formatBeijingTime(opportunity.created_at)} />
                  {opportunity.status === 'loss' && (
                    <DetailField
                      label="失单原因"
                      value={`${opportunity.loss_reason_name ?? '-'}${opportunity.loss_remark ? `（${opportunity.loss_remark}）` : ''}`}
                    />
                  )}
                </div>
              </div>
              {/* 客户决策关系图（设计稿商机详情右栏）：这单要打通谁一目了然。
                  窄屏时整块落到左栏下方，不会被压成一条 */}
              <div className="split-side">
                <DecisionMakerCard
                  customerId={opportunity.customer_id}
                  title="客户决策关系图"
                  boxed={false}
                />
              </div>
            </div>
          )}

          {activeKey === 'overview' && (
            /* 商机全景：这几张卡本来只到 640px 就结束了，下面空出半屏灰底；
               而"这单要了几个商品、开了几张报价、出了几张订单、跟了几次、剩几个任务"
               全散在别的页签里。五个数字摆一条，点一下跳过去。
               和客户详情概览同一套（主人 2026-11-17 定的方案二：把空白换成信息）。 */
            <div style={{ marginTop: 18, borderTop: '1px solid var(--crm-border)', paddingTop: 14 }}>
              <div className="customer-info-head">
                <span className="customer-info-icon" style={{ background: '#eaf1fe', color: '#2f6df6' }}>
                  <IconHistogram size="small" />
                </span>
                商机全景
                <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--crm-text-3)' }}>
                  点数字直接跳到对应页签
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
                {([
                  /* 色带：需求蓝 / 报价橙 / 订单绿 / 跟进紫 / 任务青 */
                  { key: 'items', label: '需求商品', value: itemsQuery.data?.length, tone: 'tone-primary' },
                  { key: 'records', label: '报价', value: quoteCountQuery.data?.total, tone: 'tone-caution' },
                  { key: 'records', label: '订单', value: orderCountQuery.data?.total, tone: 'tone-success' },
                  { key: 'followups', label: '跟进记录', value: followupsQuery.data?.total ?? followupsQuery.data?.items?.length, tone: 'tone-secondary' },
                  { key: 'tasks', label: '任务', value: tasksQuery.data?.total ?? tasksQuery.data?.items?.length, tone: 'tone-teal' },
                  // 报价与订单都落在「关联单据」页签里，所以这两个格子跳同一个地方
                ] as const).map((tile) => (
                  <button
                    key={tile.label}
                    type="button"
                    className={`cust-pano-tile ${tile.tone}`}
                    onClick={() => setActiveKey(tile.key)}
                    title={`跳到「${TABS.find((t) => t.itemKey === tile.key)?.tab ?? tile.key}」页签`}
                  >
                    <span className="cust-pano-label">{tile.label}</span>
                    <span className="cust-pano-value">
                      {tile.value === undefined || tile.value === null ? '-' : tile.value}
                      {tile.value !== undefined && tile.value !== null && (
                        <span className="cust-pano-unit">条</span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {activeKey === 'items' && (
            <>
              <div className="toolbar">
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                  客户要什么就记在这里，后续核价按条计算
                </div>
                <div style={{ flex: 1 }} />
                {canManage && (
                  <Button type="primary" theme="outline" onClick={() => setItemVisible(true)}>
                    添加需求商品
                  </Button>
                )}
              </div>
              <Table<OpportunityItem>
                columns={itemColumns}
                dataSource={itemsQuery.data ?? []}
                loading={itemsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyLine('还没有需求明细', '点右上「添加需求商品」，把品名、规格、数量、目标价记下来；后续核价按条算。')}
                scroll={{ x: '100%' }}
              />
            </>
          )}

          {activeKey === 'records' && <OpportunityRecords key={opportunityId}
            opportunityId={opportunityId} customerId={opportunity.customer_id} title={opportunity.title} />}

          {activeKey === 'followups' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button onClick={() => setFollowupVisible(true)}>记录跟进</Button>
              </div>
              <Table<FollowUp>
                columns={[
                  { title: '跟进时间', dataIndex: 'created_at', width: 180, render: (v: string) => formatBeijingTime(v) },
                  { title: '方式', dataIndex: 'followup_type', width: 100 },
                  { title: '内容', dataIndex: 'content' },
                  { title: '客户反馈', dataIndex: 'customer_feedback', render: (v: string | null) => v ?? '-' },
                  { title: '记录人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
                  {
                    title: '附件',
                    width: 90,
                    render: (_: unknown, record: FollowUp) => (
                      <FollowUpAttachmentsButton followupId={record.id} />
                    ),
                  },
                ]}
                dataSource={followupsQuery.data?.items ?? []}
                loading={followupsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyLine('还没有跟进记录', '每次电话、拜访或发出报价后记一条；记完会同时进时间线。')}
              />
            </>
          )}

          {activeKey === 'tasks' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button onClick={() => setTaskVisible(true)}>新建任务</Button>
              </div>
              <Table<Task>
                columns={taskColumns}
                dataSource={tasksQuery.data?.items ?? []}
                loading={tasksQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyLine('还没有任务', '点上方「新建任务」记下要做的事 —— 任务记将来，跟进记过去。')}
              />
            </>
          )}

          {activeKey === 'timeline' && (
            /* 不传 currentType 的话，来源指向本商机时会出现一个「查看原单」，
               点下去原地不动（实测过）。传进去让组件自己掐掉。 */
            <Timeline
              events={timelineQuery.data ?? []}
              loading={timelineQuery.isLoading}
              currentType="opportunity"
              currentId={opportunityId}
            />
          )}

          {activeKey === 'files' && (
            <AttachmentPanel businessType="opportunity" businessId={opportunityId} />
          )}
        </div>
      </SectionCard>

      {can('agent:use') && (
        <SectionCard title="AI 分析" style={{ marginTop: 16 }}>
          {/* 原来三个按钮并排挤在标题右边，下面一句总说明把三件事混在一起说 ——
              看不出哪个按钮管什么，点完的结果也看不出属于哪一项。
              现在三个动作各占一段：小标题 + 一句人话说明 + 右侧按钮，
              结果就落在自己那段下面（跑哪项就亮哪项）。 */}
          <div className="card-sub-head">
            <span className="card-sub-icon" style={{ background: '#eaf1fe', color: '#2f6df6' }}>
              <IconHistogram size="small" />
            </span>
            商机分析
            <Button
              size="small"
              style={{ marginLeft: 'auto' }}
              loading={aiMutation.isPending && aiKind === 'analysis'}
              onClick={() => {
                setAiKind('analysis')
                aiMutation.mutate('analysis')
              }}
            >
              运行
            </Button>
          </div>
          <div className="card-sub-desc">这单卡在哪个阶段、停了多久、有没有丢单的风险</div>
          {aiKind === 'analysis' && <AgentInsight envelope={aiEnvelope} empty="正在分析…" />}

          <div className="card-sub-divider" />

          <div className="card-sub-head">
            <span className="card-sub-icon" style={{ background: '#e6f6f2', color: '#0d9488' }}>
              <IconApps size="small" />
            </span>
            产品推荐
            <Button
              size="small"
              style={{ marginLeft: 'auto' }}
              loading={aiMutation.isPending && aiKind === 'recommend'}
              onClick={() => {
                setAiKind('recommend')
                aiMutation.mutate('recommend')
              }}
            >
              运行
            </Button>
          </div>
          <div className="card-sub-desc">按这家客户过去成交过什么，给这次该推的产品</div>
          {aiKind === 'recommend' && <AgentInsight envelope={aiEnvelope} empty="正在推荐…" />}

          <div className="card-sub-divider" />

          <div className="card-sub-head">
            <span className="card-sub-icon" style={{ background: '#f1ecfe', color: '#7a4fd3' }}>
              <IconFile size="small" />
            </span>
            报价草稿建议
            <Button
              size="small"
              style={{ marginLeft: 'auto' }}
              loading={aiMutation.isPending && aiKind === 'draft'}
              onClick={() => {
                setAiKind('draft')
                aiMutation.mutate('draft')
              }}
            >
              运行
            </Button>
          </div>
          <div className="card-sub-desc">按需求商品给一份报价草稿，只给建议、不落库</div>
          {aiKind === 'draft' && <AgentInsight envelope={aiEnvelope} empty="正在起草…" />}
        </SectionCard>
      )}

      <FollowUpModal
        visible={followupVisible}
        onClose={() => setFollowupVisible(false)}
        target={{ opportunityId, customerId: opportunity.customer_id }}
        onCreated={refresh}
      />

      <Modal
        width={520}
        title="推进阶段"
        visible={stageVisible}
        onCancel={() => setStageVisible(false)}
        onOk={() => {
          if (!targetStage) {
            Toast.warning('请选择目标阶段')
            return
          }
          stageMutation.mutate()
        }}
        confirmLoading={stageMutation.isPending}
        okText="推进"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            当前阶段：{opportunity.stage_name}
          </div>
          <Select
            placeholder="选择目标阶段"
            value={targetStage ?? undefined}
            onChange={(value) => setTargetStage(value as number)}
            optionList={(stagesQuery.data ?? [])
              // 成交 / 失单不能从「推进阶段」走（第十二批 12.2）：那两条路各有正式
              // 入口 —— 确认成交要按报价版本校验并生成订单，标记失单要填失单原因。
              // 这里**只是操作引导**，后端另有一道拦截，直接调接口同样过不去。
              .filter(
                (stage) =>
                  stage.status === 'active' && stage.id !== opportunity.stage_id &&
                  !stage.is_win && !stage.is_loss,
              )
              .map((stage) => ({ value: stage.id, label: stage.name }))}
            style={{ width: '100%' }}
          />
          <Input
            value={stageRemark}
            onChange={setStageRemark}
            placeholder="推进说明（可不填）"
          />
        </div>
      </Modal>

      <Modal
        title="确认成交并生成订单"
        visible={confirmWinVisible}
        onCancel={() => setConfirmWinVisible(false)}
        onOk={() => confirmWinMutation.mutate()}
        confirmLoading={confirmWinMutation.isPending}
        okText="确认成交并建单"
        okButtonProps={{ disabled: planNotReady }}
        width={680}
        footer={
          <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <Button onClick={() => setConfirmWinVisible(false)}>取消</Button>
              {/* 2026-10-15 改成共用件：这一处原来是八行手写（两层 div +
                  `pointerEvents: 'none'` + CSS 类名 + 单独一块 BlockedHint），
                  而新页面照抄时总漏（一轮补了三个文件）。`GuardedButton` 把
                  「旁边常驻灰字」与「点一下弹提示」都包在里面了。 */}
              <GuardedButton
                theme="solid"
                loading={confirmWinMutation.isPending}
                disabled={planNotReady}
                blockedReasons={planBlocked}
                action="确认成交并建单"
                onClick={() => confirmWinMutation.mutate()}
              >
                确认成交并建单
              </GuardedButton>
            </div>
          </div>
        }
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
            将把该商机最新已发送/已接受的报价版本转为销售订单，并复制全部明细；
            此前已建单则直接返回原订单（不会重复建）。
          </div>
          <ReceivablePlanPicker
            mode={planMode}
            onModeChange={setPlanMode}
            rows={planRows}
            onRowsChange={setPlanRows}
          />
        </div>
      </Modal>

      <Modal
        width={520}
        title="标记失单"
        visible={loseVisible}
        onCancel={() => setLoseVisible(false)}
        onOk={() => {
          if (!lossReasonId) {
            Toast.warning('失单原因必选')
            return
          }
          loseMutation.mutate()
        }}
        confirmLoading={loseMutation.isPending}
        okText="确认失单"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Select
            placeholder="失单原因"
            value={lossReasonId ?? undefined}
            onChange={(value) => setLossReasonId(value as number)}
            optionList={(lossReasonsQuery.data ?? []).map((reason) => ({
              value: reason.id,
              label: reason.name,
            }))}
            loading={lossReasonsQuery.isLoading}
            style={{ width: '100%' }}
          />
          <TextArea value={lossRemark} onChange={setLossRemark} rows={2} placeholder="补充说明" />
        </div>
      </Modal>

      <Modal
        title="添加需求商品"
        visible={itemVisible}
        width={680}
        onCancel={() => setItemVisible(false)}
        onOk={() => {
          if (!itemForm.sku_id) {
            Toast.warning('请选择 SKU')
            return
          }
          itemMutation.mutate()
        }}
        confirmLoading={itemMutation.isPending}
        okText="添加"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>SKU</FormLabel>
            <Select
              placeholder="选择 SKU"
              value={itemForm.sku_id ?? undefined}
              onChange={(value) => {
                const sku = (skusQuery.data?.items ?? []).find((item) => item.id === value)
                setItemForm({
                  ...itemForm,
                  sku_id: value as number,
                  specification: sku?.specification ?? itemForm.specification,
                  color: sku?.color ?? itemForm.color,
                })
              }}
              optionList={(skusQuery.data?.items ?? []).map((sku) => ({
                value: sku.id,
                label: skuLabel(sku),
              }))}
              loading={skusQuery.isLoading}
              filter={optionMatcher}
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>数量</div>
              <Input
                value={itemForm.quantity}
                onChange={(v) => setItemForm({ ...itemForm, quantity: v })}
                placeholder="例如 3000"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户目标价（元）</div>
              <Input
                value={itemForm.target_price}
                onChange={(v) => setItemForm({ ...itemForm, target_price: v })}
                placeholder="客户希望的价格"
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>规格</div>
              <Input
                value={itemForm.specification}
                onChange={(v) => setItemForm({ ...itemForm, specification: v })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>颜色</div>
              <Input value={itemForm.color} onChange={(v) => setItemForm({ ...itemForm, color: v })} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>包装要求</div>
              <Input
                value={itemForm.package_requirement}
                onChange={(v) => setItemForm({ ...itemForm, package_requirement: v })}
                placeholder="例如：20 个一包"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>交期</div>
              <DatePicker
                value={itemForm.delivery_date ?? undefined}
                onChange={(date) => setItemForm({ ...itemForm, delivery_date: (date as Date) ?? null })}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>目的地</div>
              <Input
                value={itemForm.destination}
                onChange={(v) => setItemForm({ ...itemForm, destination: v })}
                placeholder="例如：浙江宁波"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>备注</div>
              <Input value={itemForm.remark} onChange={(v) => setItemForm({ ...itemForm, remark: v })} />
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        width={520}
        title="新建任务"
        visible={taskVisible}
        onCancel={() => setTaskVisible(false)}
        onOk={() => {
          if (!taskForm.title.trim()) {
            Toast.warning('任务标题必填')
            return
          }
          taskMutation.mutate()
        }}
        confirmLoading={taskMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>任务标题</FormLabel>
            <Input
              value={taskForm.title}
              onChange={(v) => setTaskForm({ ...taskForm, title: v })}
              placeholder="例如：给客户寄样"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div>
              {/* 与任务列表页的「新建任务」弹窗保持一字不差：
                  priority 不影响排序/提醒（全库查过），只能说成"轻重标记"。
                  ⚠️ 改这里记得同步 TaskListPage.tsx。 */}
              <FormLabel hint="仅作轻重标记，不影响排序和提醒">优先级</FormLabel>
              <Select
                value={taskForm.priority}
                onChange={(value) => setTaskForm({ ...taskForm, priority: value as string })}
                optionList={[
                  { value: 'high', label: '高' },
                  { value: 'normal', label: '中' },
                  { value: 'low', label: '低' },
                ]}
                style={{ width: 140 }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel>截止时间</FormLabel>
              <DatePicker
                type="dateTime"
                value={taskForm.due_at ?? undefined}
                onChange={(date) => setTaskForm({ ...taskForm, due_at: (date as Date) ?? null })}
                placeholder="选择时间"
                style={{ width: '100%' }}
              />
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}
