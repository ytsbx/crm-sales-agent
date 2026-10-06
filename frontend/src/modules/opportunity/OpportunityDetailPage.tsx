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

import { getOpportunityTimeline, listFollowups, type FollowUp } from '../../shared/api/followup'
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
import SectionCard from '../../shared/components/SectionCard'
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
          ? itemForm.delivery_date.toISOString().slice(0, 10)
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
  if (opportunityQuery.isLoading) return <div className="page-container">加载中…</div>
  if (!opportunity) return <div className="page-container">商机不存在或无权查看</div>

  const itemColumns = [
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 130 },
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
            {new Date(value).toLocaleString('zh-CN')}
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
          <a style={{ color: 'var(--crm-primary)' }} onClick={() => completeTaskMutation.mutate(record.id)}>
            完成
          </a>
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
                ? `¥${opportunity.expected_amount.toLocaleString('zh-CN')}`
                : '-'}
            </span>
            <span>预计成交：{opportunity.expected_close_date ?? '-'}</span>
            <span>需求条数：{opportunity.item_count}</span>
          </>
        }
        extra={
          <>
            <Button onClick={() => setFollowupVisible(true)}>记录跟进</Button>
            <Button
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
                  onClick={() => {
                    setTargetStage(null)
                    setStageVisible(true)
                  }}
                >
                  推进阶段
                </Button>
                <Button onClick={() => setTaskVisible(true)}>新建任务</Button>
                {opportunity.status === 'open' && (
                  <>
                    <Popconfirm
                      title="确认成交并生成订单？"
                      content="将把该商机最新已发送/已接受的报价版本转为销售订单，此前已建单则直接返回原订单"
                      onConfirm={() => confirmWinMutation.mutate()}
                    >
                      <Button theme="solid" loading={confirmWinMutation.isPending}>
                        确认成交并建单
                      </Button>
                    </Popconfirm>
                    <Button type="danger" onClick={() => setLoseVisible(true)}>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 24 }}>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: 20,
                  alignContent: 'start',
                }}
              >
                <DetailField label="商机名称" value={opportunity.title} />
                <DetailField label="客户" value={opportunity.customer_name} />
                <DetailField label="当前阶段" value={opportunity.stage_name} />
                <DetailField
                  label="预计金额"
                  value={opportunity.expected_amount ? `¥${opportunity.expected_amount.toLocaleString('zh-CN')}` : '-'}
                />
                <DetailField label="预计成交日期" value={opportunity.expected_close_date ?? '-'} />
                <DetailField label="竞争对手" value={opportunity.competitor ?? '-'} />
                <DetailField label="下一步动作" value={opportunity.next_action ?? '-'} />
                <DetailField label="来源" value={opportunity.source ?? '-'} />
                <DetailField label="创建时间" value={new Date(opportunity.created_at).toLocaleString('zh-CN')} />
                {opportunity.status === 'loss' && (
                  <DetailField
                    label="失单原因"
                    value={`${opportunity.loss_reason_name ?? '-'}${opportunity.loss_remark ? `（${opportunity.loss_remark}）` : ''}`}
                  />
                )}
              </div>
              {/* 客户决策关系图（设计稿商机详情右栏）：这单要打通谁一目了然 */}
              <DecisionMakerCard
                customerId={opportunity.customer_id}
                title="客户决策关系图"
                boxed={false}
              />
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
                  <Button theme="solid" onClick={() => setItemVisible(true)}>
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
                empty="还没有需求明细"
                scroll={{ x: 1200 }}
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
                  { title: '时间', dataIndex: 'created_at', width: 180, render: (v: string) => new Date(v).toLocaleString('zh-CN') },
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
                empty="还没有跟进记录"
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
                empty="还没有任务"
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
        <SectionCard
          title="AI 分析"
          style={{ marginTop: 16 }}
          extra={
            <>
              <Button
                size="small"
                loading={aiMutation.isPending}
                onClick={() => aiMutation.mutate('analysis')}
              >
                商机分析
              </Button>
              <Button
                size="small"
                loading={aiMutation.isPending}
                onClick={() => aiMutation.mutate('recommend')}
              >
                产品推荐
              </Button>
              <Button
                size="small"
                loading={aiMutation.isPending}
                onClick={() => aiMutation.mutate('draft')}
              >
                报价草稿建议
              </Button>
            </>
          }
        >
          <AgentInsight
            envelope={aiEnvelope}
            empty="点右上角按钮运行：商机分析看阶段停留与风险；产品推荐按客户成交历史；报价草稿建议只给建议不落库"
          />
        </SectionCard>
      )}

      <FollowUpModal
        visible={followupVisible}
        onClose={() => setFollowupVisible(false)}
        target={{ opportunityId, customerId: opportunity.customer_id }}
        onCreated={refresh}
      />

      <Modal
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
              .filter((stage) => stage.id !== opportunity.stage_id)
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
        width={620}
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
                label: `${sku.product_name ?? ''} ${sku.sku_code} ${sku.specification ?? ''}`,
              }))}
              loading={skusQuery.isLoading}
              filter
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
