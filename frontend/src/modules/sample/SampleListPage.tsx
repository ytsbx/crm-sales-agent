import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { emptyText } from '../../shared/hooks/emptyText'
import {
  Button,
  DatePicker,
  Input,
  Modal,
  Select,
  SideSheet,
  Table,
  Tag,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import { listCustomInquiries } from '../../shared/api/inquiry'
import { getOpportunity, listOpportunities } from '../../shared/api/opportunity'
import { listSkusForPricing } from '../../shared/api/pricing'
import { reportOperationTiming } from '../../shared/api/analytics'
import {
  addSampleItem,
  approveSample,
  confirmSample,
  createSample,
  feedbackSample,
  getSample,
  listSamples,
  madeSample,
  resubmitSample,
  reviseSample,
  shipSample,
  signSample,
  updateSample,
  updateSampleItem,
  type BasisFile,
  type SampleItem,
  type SampleRequestRow,
} from '../../shared/api/sample'
import { listBusinessFiles } from '../../shared/api/file'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { usePermissions } from '../../shared/hooks/permissions'
import BizDocPanel from '../../shared/components/BizDocPanel'
import type { TagTone } from '../../shared/types'
import { optionMatcher } from '../../shared/components/optionMatch'

/** 样品（PRD §19 / 03-API §26）。 */

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  approved: 'blue',
  rejected: 'red',
  shipped: 'cyan',
  signed: 'green',
}

const STATUS_OPTIONS = [
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'shipped', label: '已寄样' },
  { value: 'signed', label: '已签收' },
]

/**
 * 明细草稿。两条路径（场景09）：现货选 SKU；定制件尚无 SKU，选需求编号。
 * 定制件本来就要先打样再定 SKU，强制先建档等于把顺序反过来。
 */
type ItemDraft = {
  mode: 'sku' | 'custom'
  sku_id?: number
  inquiry_id?: number
  item_name?: string
  quantity: string
}

function draftReady(item: ItemDraft): boolean {
  return item.mode === 'custom' ? Boolean(item.inquiry_id) : Boolean(item.sku_id)
}

function draftPayload(item: ItemDraft): Record<string, unknown> {
  const quantity = Number(item.quantity) || 1
  if (item.mode === 'custom') {
    return {
      inquiry_id: item.inquiry_id,
      item_name: item.item_name?.trim() || null,
      quantity,
    }
  }
  return { sku_id: item.sku_id, quantity }
}

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '-'
}

/**
 * 保存资料后给一句提示，只在状态**真的变了**的时候才说。
 *
 * 不能拿返回后的 status === 'pending' 当判断依据：待审批的单子改完状态还是
 * 「待审批」，那样会弹「已退回待审批」—— 明明什么都没退。要对比保存前的状态。
 */
function reopenedHint(before: string | undefined, after: string): string | null {
  if (after !== 'pending' || (before !== 'approved' && before !== 'rejected')) return null
  return before === 'rejected'
    ? '已保存；单据已重新提交「待审批」，等待重新审批'
    : '已保存；因修改车间依据，单据已退回「待审批」'
}

/** 生产资料这类"可有可无"的字段统一显示成 —，而不是一片空白。 */
function dash(value?: string | null) {
  return value && value.trim() ? value : '—'
}

export default function SampleListPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('sample:manage')

  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const params = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawCreateOpportunityId = Number(searchParams.get('create_opportunity_id'))
  const createOpportunityId = Number.isSafeInteger(rawCreateOpportunityId) && rawCreateOpportunityId > 0 ? rawCreateOpportunityId : undefined
  const createOpportunityQuery = useQuery({
    queryKey: ['opportunity', createOpportunityId],
    queryFn: () => getOpportunity(createOpportunityId!), enabled: Boolean(createOpportunityId) && canManage,
  })
  const [createVisible, setCreateVisible] = useState(false)
  /** 打样申请的计时起点（场景18 操作耗时埋点） */
  const createStartedAt = useRef<number | null>(null)
  const [form, setForm] = useState<{
    opportunity_id?: number
    customer_id?: number
    remark: string
    items: ItemDraft[]
  }>({ remark: '', items: [{ mode: 'sku', quantity: '1' }] })
  const selectedOpportunityQuery = useQuery({
    queryKey: ['opportunity', form.opportunity_id],
    queryFn: () => getOpportunity(form.opportunity_id!),
    enabled: createVisible && Boolean(form.opportunity_id),
  })

  useEffect(() => {
    if (!createOpportunityId || !createOpportunityQuery.data || !canManage) return
    setForm({ opportunity_id: createOpportunityId, customer_id: createOpportunityQuery.data.customer_id,
      remark: '', items: [{ mode: 'sku', quantity: '1' }] })
    createStartedAt.current = Date.now()
    setCreateVisible(true)
    const next = new URLSearchParams(searchParams)
    next.delete('create_opportunity_id')
    setSearchParams(next, { replace: true })
  }, [createOpportunityId, createOpportunityQuery.data, canManage, searchParams, setSearchParams])

  useEffect(() => {
    if (createOpportunityQuery.error) Toast.error(createOpportunityQuery.error.message)
  }, [createOpportunityQuery.error])

  const [detailId, setDetailId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [shipForm, setShipForm] = useState({ carrier: '', tracking_no: '', shipping_fee: '' })
  const [feedback, setFeedback] = useState('')
  const [newItem, setNewItem] = useState<ItemDraft>({ mode: 'sku', quantity: '1' })
  // 生产打样资料（文档 §3.5）：跟单在这一栏把车间要的东西补全，
  // 打样需求单出图时逐项带给车间。
  // 材质 / 工艺 / 图纸版本**不在这里**：它们逐行不同，在明细行上改（见 itemEdit）。
  const [prodForm, setProdForm] = useState({
    purpose: '',
    target_completion_date: '',
    acceptance_criteria: '',
    sample_fee: '',
  })
  const [prodEditing, setProdEditing] = useState(false)
  // 改某一条明细的车间依据（材质 / 工艺 / 图纸版本）
  const [itemEdit, setItemEdit] = useState<{
    id: number
    craft: string
    material: string
    drawing_version: string
  } | null>(null)
  const [confirmRemark, setConfirmRemark] = useState('')

  const query = useQuery({
    queryKey: ['samples', { status, keyword, page, pageSize }],
    queryFn: () =>
      listSamples({
        status,
        keyword: keyword.trim() || undefined,
        page,
        page_size: pageSize,
      }),
  })

  // 深链 /samples/:id：路由带 id 进来时直接打开详情抽屉
  useEffect(() => {
    const routeId = Number(params.id)
    if (Number.isFinite(routeId) && routeId > 0) setDetailId(routeId)
  }, [params.id])

  const closeDetail = () => {
    setDetailId(null)
    // 深链进来的（地址栏是 /samples/:id）关掉后回到列表，别留在一条空路由上
    if (params.id) navigate('/samples')
  }

  // 详情优先复用列表数据里的完整对象（列表已返回 items/shipments），避免多打一次接口；
  // 深链打开的那条如果不在当前列表页，再用 getSample 补一次。
  const listDetail: SampleRequestRow | undefined = (query.data?.items ?? []).find(
    (row) => row.id === detailId,
  )
  const detailQuery = useQuery({
    queryKey: ['sample-detail', detailId],
    queryFn: () => getSample(detailId!),
    enabled: detailId !== null && listDetail === undefined && !query.isLoading,
  })
  const detail: SampleRequestRow | undefined = listDetail ?? detailQuery.data

  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['opportunities-for-select'],
    queryFn: () => listOpportunities({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })
  const skusQuery = useQuery({ queryKey: ['skus-for-pricing'], queryFn: listSkusForPricing })
  // 定制需求下拉：只有打开新建弹窗或详情抽屉时才拉
  const inquiriesQuery = useQuery({
    queryKey: ['inquiries-for-sample'],
    queryFn: () => listCustomInquiries({ page: 1, page_size: 100 }),
    enabled: createVisible || detailId !== null,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['timeline', 'customer'] })
    return queryClient.invalidateQueries({ queryKey: ['samples'] })
  }
  const onError = (error: Error) => Toast.error(error.message)

  const createMutation = useMutation({
    mutationFn: () =>
      createSample({
        opportunity_id: form.opportunity_id ?? null,
        customer_id: form.customer_id ?? null,
        remark: form.remark.trim() || null,
        items: form.items
          .filter(draftReady)
          .map(draftPayload),
      }),
    onSuccess: (created) => {
      // 计时上报（场景18）：起点在"点新建"那一刻。上报失败不打扰业务——
      // 埋点是量尺，不该变成新的故障点。
      const startedAt = createStartedAt.current
      createStartedAt.current = null
      if (startedAt) {
        const duration = Date.now() - startedAt
        if (duration > 0 && duration <= 8 * 3600 * 1000) {
          void reportOperationTiming({
            operation: 'sample_create',
            duration_ms: duration,
            business_type: 'sample',
            business_id: created?.id ?? null,
            // 手输字段数：备注 + 每个有 SKU 的明细行各算 1
            typed_fields:
              (form.remark.trim() ? 1 : 0) + form.items.filter(draftReady).length,
          }).catch(() => undefined)
        }
      }
      Toast.success('样品申请已创建')
      setCreateVisible(false)
      setForm({ remark: '', items: [{ mode: 'sku', quantity: '1' }] })
      void refresh()
    },
    onError,
  })

  const approveMutation = useMutation({
    mutationFn: (approved: boolean) =>
      approveSample(detailId!, approved, approved ? undefined : rejectReason),
    onSuccess: (_data, approved) => {
      Toast.success(approved ? '样品已批准' : '样品已拒绝')
      setRejectReason('')
      void refresh()
    },
    onError,
  })

  // 已驳回原样重提：和「改资料自动回待审批」是两条路，这条一个字都不改，
  // 专门给"认为驳回理由不成立"的跟单用
  const resubmitMutation = useMutation({
    mutationFn: () => resubmitSample(detailId!),
    onSuccess: () => {
      Toast.success('已重新提交，等待审批')
      void refresh()
    },
    onError,
  })

  const shipMutation = useMutation({
    mutationFn: () =>
      shipSample(detailId!, {
        carrier: shipForm.carrier.trim() || null,
        tracking_no: shipForm.tracking_no.trim() || null,
        shipping_fee: shipForm.shipping_fee.trim() ? Number(shipForm.shipping_fee) : 0,
      }),
    onSuccess: () => {
      Toast.success('已登记寄样')
      setShipForm({ carrier: '', tracking_no: '', shipping_fee: '' })
      void refresh()
    },
    onError,
  })

  const signMutation = useMutation({
    mutationFn: () => signSample(detailId!),
    onSuccess: () => {
      Toast.success('已登记签收')
      void refresh()
    },
    onError,
  })

  const feedbackMutation = useMutation({
    mutationFn: () => feedbackSample(detailId!, feedback),
    onSuccess: () => {
      Toast.success('反馈已登记')
      setFeedback('')
      void refresh()
    },
    onError,
  })

  // 生产打样资料（文档 §3.5）：跟单在这一栏把车间要的东西补全，
  // 打样需求单出图时逐项带给车间
  const saveProdMutation = useMutation({
    mutationFn: () => {
      // 「空着 = 未填」是业务确认过的口径：留空就发 null，不要偷偷当 0 存。
      // 负数和非法数字在前端先挡一道，省得跑一趟后端才看到 422。
      const raw = prodForm.sample_fee.trim()
      const fee = raw === '' ? null : Number(raw)
      if (fee !== null && (!Number.isFinite(fee) || fee < 0)) {
        throw new Error('打样费用要填一个不小于 0 的数字（留空表示「未填」）')
      }
      return updateSample(detailId!, {
        purpose: prodForm.purpose || null,
        target_completion_date: prodForm.target_completion_date || null,
        acceptance_criteria: prodForm.acceptance_criteria || null,
        sample_fee: fee,
      })
    },
    onSuccess: (row: SampleRequestRow) => {
      // 后端口径：改「车间依据」（目标完成日 / 验收标准）会让已批准的单子退回待审批；
      // 已驳回的单子改任何一项都算重新提交。提示里必须说清楚，否则用户只会看到
      // 状态自己变了，以为系统出错。
      Toast.success(reopenedHint(detail?.status, row.status) ?? '生产资料已保存')
      setProdEditing(false)
      void refresh()
    },
    onError,
  })

  // 改明细的车间依据：与单头资料同一套闸门（后端 _gate_part_lock）
  const saveItemMutation = useMutation({
    mutationFn: () =>
      updateSampleItem(detailId!, itemEdit!.id, {
        // 留空 = 没填，发 null 把它清掉；这是「空着=未填」口径的一部分
        craft: itemEdit!.craft.trim() || null,
        material: itemEdit!.material.trim() || null,
        drawing_version: itemEdit!.drawing_version.trim() || null,
      }),
    onSuccess: (row: SampleRequestRow) => {
      Toast.success(reopenedHint(detail?.status, row.status) ?? '车间依据已保存')
      setItemEdit(null)
      void refresh()
    },
    onError,
  })

  // 登记制作完成（含**制作依据**，第一批返修 §3.5）。
  // 为什么必须弹窗而不是直接提交：依据是"这次照哪几份图纸做的"，事后出了质量问题
  // 拿什么比对全看这一笔。原来页面只发 remark、连 remark 都没传，
  // 快照永远是空的 —— 后端字段白做了（返工单 P2-7）。
  const [madeModal, setMadeModal] = useState(false)
  const [madeForm, setMadeForm] = useState<{ remark: string; fileIds: number[] }>({
    remark: '',
    fileIds: [],
  })
  // 这张打样单上已挂的附件：只有挂在本单上的文件才能作为它的制作依据
  // （后端会校验这一点，前端不列出来等于让人猜文件名）
  const madeFilesQuery = useQuery({
    queryKey: ['sample-files', detailId],
    queryFn: () => listBusinessFiles('sample', detailId!),
    enabled: madeModal && Boolean(detailId),
  })

  const madeMutation = useMutation({
    mutationFn: () =>
      madeSample(detailId!, madeForm.remark.trim() || undefined, madeForm.fileIds),
    onSuccess: () => {
      Toast.success(
        madeForm.fileIds.length
          ? `已登记制作完成，并记下 ${madeForm.fileIds.length} 份制作依据`
          : '已登记制作完成（未指定制作依据）',
      )
      setMadeModal(false)
      setMadeForm({ remark: '', fileIds: [] })
      void refresh()
    },
    onError,
  })

  // 开新修订版（§3.3）：已制作/已寄出之后改车间依据的唯一出路。
  // 后端会拒绝原地改并点名这个接口，界面没有入口就等于把正常操作堵死。
  // 备注可选，这里不额外开弹窗（是否要写原因的交互留给人自己决定）
  const reviseMutation = useMutation({
    mutationFn: () => reviseSample(detailId!),
    onSuccess: (row: SampleRequestRow) => {
      Toast.success(`已开第 ${row.version ?? '?'} 版待审批（原版冻结保留）`)
      setDetailId(row.id)          // 直接切到新版本上继续操作
      navigate(`/samples/${row.id}`)
      void refresh()
    },
    onError,
  })

  // 客户确认与签收分开：客户收到样品 ≠ 客户接受（后端也会拦"未签收就确认"）
  const confirmMutation = useMutation({
    mutationFn: (accepted: boolean) => confirmSample(detailId!, accepted, confirmRemark),
    onSuccess: (_row: SampleRequestRow, accepted: boolean) => {
      Toast.success(accepted ? '已登记：客户接受' : '已登记：客户未通过')
      setConfirmRemark('')
      void refresh()
    },
    onError,
  })

  const addItemMutation = useMutation({
    mutationFn: () => addSampleItem(detailId!, draftPayload(newItem)),
    onSuccess: (row: SampleRequestRow) => {
      // 往已批准 / 已驳回的单子里加明细也会触发重批：新明细带进来的材质工艺图纸版本
      // 同样是车间依据（后端走同一道闸门）。这里也要说清楚，别让用户以为状态自己变了。
      Toast.success(reopenedHint(detail?.status, row.status) ?? '明细已添加')
      setNewItem({ mode: 'sku', quantity: '1' })
      void refresh()
    },
    onError,
  })

  const columns = [
    {
      title: '客户 / 商机',
      dataIndex: 'customer_name',
      render: (value: string | null, record: SampleRequestRow) => (
        <div>
          <div style={{ fontWeight: 600 }}>{value ?? '-'}</div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            {record.opportunity_title ?? '未关联商机'}
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string, record: SampleRequestRow) => (
        <Tag size="small" color={STATUS_COLOR[record.status] ?? 'grey'}>
          {value}
        </Tag>
      ),
    },
    {
      title: '样品明细',
      dataIndex: 'items',
      render: (items: SampleRequestRow['items']) =>
        items.length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>无明细</span>
        ) : (
          <span style={{ fontSize: 12 }}>
            {items.map((item) => `${item.sku_code ?? item.sku_id} ×${item.quantity}`).join('、')}
          </span>
        ),
    },
    {
      title: '快递',
      width: 170,
      render: (_: unknown, record: SampleRequestRow) => {
        const shipment = record.shipments[0]
        if (!shipment) return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        return (
          <span style={{ fontSize: 12 }}>
            {shipment.carrier ?? '-'}
            <br />
            {shipment.tracking_no ?? '-'}
          </span>
        )
      },
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
    { title: '申请时间', dataIndex: 'requested_at', width: 170, render: fmt },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record: SampleRequestRow) => (
        <Button
          theme="borderless"
          size="small"
          onClick={() => {
            setDetailId(record.id)
            navigate(`/samples/${record.id}`)
          }}
        >
          详情
        </Button>
      ),
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    label: `${sku.sku_code}${sku.specification ? ` · ${sku.specification}` : ''}`,
  }))
  const inquiryOptions = (inquiriesQuery.data?.items ?? []).map((row) => ({
    value: row.id,
    label: `${row.inquiry_no ?? `#${row.id}`} · ${row.title}`,
  }))

  return (
    <div className="page-container">
      <PageHeader
        title="样品管理"
        subtitle="样品申请、寄样、签收与反馈；样品进展会回写到商机的下一步动作"
        extra={
          canManage && (
            <Button
              theme="solid"
              type="primary"
              onClick={() => {
                // 耗时埋点（场景18）：起点在这里——点开"新建样品申请"那一刻
                createStartedAt.current = Date.now()
                setCreateVisible(true)
              }}
            >
              新建样品申请
            </Button>
          )
        }
      />

      <SectionCard>
        <div className="toolbar">
          <Input
            style={{ width: 220 }}
            placeholder="搜索客户或商机"
            value={keyword}
            onChange={setKeyword}
            showClear
          />
          <Select
            style={{ width: 150 }}
            placeholder="全部状态"
            showClear
            value={status}
            onChange={(value) => setStatus(value as string | undefined)}
            optionList={STATUS_OPTIONS}
          />
        </div>

        <Table
          size="small"
          rowKey="id"
          loading={query.isLoading}
          columns={columns}
          dataSource={query.data?.items ?? []}
          empty={emptyText(query, '还没有样品申请')}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
            onPageSizeChange: (size: number) => {
              setPageSize(size)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      {/* 新建申请 */}
      <Modal
        title="新建样品申请"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => createMutation.mutate()}
        confirmLoading={createMutation.isPending}
        okText="创建"
        width={560}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>关联商机</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择商机（会自动带出客户）"
              filter={optionMatcher}
              showClear
              value={form.opportunity_id}
              onChange={(value) => {
                const id = value as number | undefined
                const opp = opportunitiesQuery.data?.items.find((row) => row.id === id)
                  ?? (selectedOpportunityQuery.data?.id === id ? selectedOpportunityQuery.data : undefined)
                setForm({ ...form, opportunity_id: id, customer_id: opp?.customer_id ?? form.customer_id })
              }}
              optionList={Array.from(new Map([
                ...(opportunitiesQuery.data?.items ?? []),
                ...(selectedOpportunityQuery.data ? [selectedOpportunityQuery.data] : []),
              ].map((opp) => [opp.id, opp])).values()).map((opp) => ({
                value: opp.id,
                label: `${opp.title}${opp.customer_name ? ` · ${opp.customer_name}` : ''}`,
              }))}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              客户（不选商机时必填）
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择客户"
              filter={optionMatcher}
              showClear
              value={form.customer_id}
              disabled={Boolean(form.opportunity_id)}
              onChange={(value) => setForm({ ...form, customer_id: value as number | undefined })}
              optionList={Array.from(new Map([
                ...(customersQuery.data?.items ?? []).map((customer) => [customer.id, { value: customer.id, label: customer.name }] as const),
                ...(selectedOpportunityQuery.data ? [[selectedOpportunityQuery.data.customer_id, {
                  value: selectedOpportunityQuery.data.customer_id,
                  label: selectedOpportunityQuery.data.customer_name ?? `客户 #${selectedOpportunityQuery.data.customer_id}`,
                }] as const] : []),
              ]).values())}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>样品明细</div>
            {form.items.map((item, index) => (
              <div key={index} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <Select
                  style={{ width: 92 }}
                  value={item.mode}
                  onChange={(value) => {
                    const items = [...form.items]
                    items[index] = {
                      ...items[index],
                      mode: value as ItemDraft['mode'],
                      sku_id: undefined,
                      inquiry_id: undefined,
                    }
                    setForm({ ...form, items })
                  }}
                  optionList={[
                    { value: 'sku', label: '现货 SKU' },
                    { value: 'custom', label: '定制需求' },
                  ]}
                />
                {item.mode === 'custom' ? (
                  <Select
                    style={{ flex: 1 }}
                    placeholder="选择定制需求（编号）"
                    filter={optionMatcher}
                    value={item.inquiry_id}
                    onChange={(value) => {
                      const items = [...form.items]
                      items[index] = { ...items[index], inquiry_id: value as number }
                      setForm({ ...form, items })
                    }}
                    optionList={inquiryOptions}
                  />
                ) : (
                  <Select
                    style={{ flex: 1 }}
                    placeholder="选择 SKU"
                    filter={optionMatcher}
                    value={item.sku_id}
                    onChange={(value) => {
                      const items = [...form.items]
                      items[index] = { ...items[index], sku_id: value as number }
                      setForm({ ...form, items })
                    }}
                    optionList={skuOptions}
                  />
                )}
                <Input
                  style={{ width: 90 }}
                  value={item.quantity}
                  onChange={(value) => {
                    const items = [...form.items]
                    items[index] = { ...items[index], quantity: value }
                    setForm({ ...form, items })
                  }}
                />
                {form.items.length > 1 && (
                  <Button
                    theme="borderless"
                    type="danger"
                    onClick={() =>
                      setForm({ ...form, items: form.items.filter((_, i) => i !== index) })
                    }
                  >
                    删除
                  </Button>
                )}
              </div>
            ))}
            <Button
              theme="borderless"
              onClick={() =>
                setForm({ ...form, items: [...form.items, { mode: 'sku', quantity: '1' }] })
              }
            >
              + 添加一行
            </Button>
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>备注</div>
            <TextArea
              value={form.remark}
              onChange={(value) => setForm({ ...form, remark: value })}
              rows={2}
            />
          </div>
        </div>
      </Modal>

      {/* 详情与流转 */}
      <SideSheet
        title={detail ? `样品申请 #${detail.id}` : '样品申请'}
        visible={detailId !== null}
        onCancel={closeDetail}
        width={760}
      >
        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <Tag size="small" color={STATUS_COLOR[detail.status] ?? 'grey'}>
                {detail.status_label}
              </Tag>
              <span style={{ marginLeft: 8, color: 'var(--crm-text-2)' }}>
                {detail.customer_name ?? '-'}
                {detail.opportunity_title ? ` · ${detail.opportunity_title}` : ''}
              </span>
            </div>

            <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
              <div>负责人：{detail.owner_name ?? '-'}</div>
              <div>申请时间：{fmt(detail.requested_at)}</div>
              {detail.approved_at && <div>审批时间：{fmt(detail.approved_at)}</div>}
              {detail.shipped_at && <div>寄样时间：{fmt(detail.shipped_at)}</div>}
              {detail.signed_at && <div>签收时间：{fmt(detail.signed_at)}</div>}
              {detail.reject_reason && (
                <div style={{ color: 'var(--crm-error)' }}>
                  {detail.status === 'rejected' ? '拒绝原因' : '上次驳回原因'}：
                  {detail.reject_reason}
                </div>
              )}
              {detail.remark && <div>备注：{detail.remark}</div>}
            </div>

            <div>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>样品明细</div>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={detail.items}
                empty="暂无明细"
                columns={[
                  {
                    title: 'SKU / 需求',
                    dataIndex: 'sku_code',
                    render: (v: string | null, row: SampleRequestRow['items'][number]) => (
                      <span>
                        {v ?? '-'}
                        {row.is_custom && (
                          <Tag size="small" style={{ marginLeft: 6 }}>
                            定制
                          </Tag>
                        )}
                      </span>
                    ),
                  },
                  { title: '原采购数量', dataIndex: 'original_quantity', width: 110, render: (v: number | null) => v == null ? '未记录' : v.toLocaleString('zh-CN') },
                  { title: '本次样品数量', dataIndex: 'quantity', width: 110 },
                  {
                    // 车间依据逐行不同：一单里两个盒子可能材质、工艺、图纸都不一样，
                    // 所以显示在明细行上，而不是单头一栏。
                    title: '车间依据',
                    render: (_: unknown, row: SampleItem) => {
                      const spec = [row.material, row.craft].filter(Boolean).join(' / ')
                      if (!row.drawing_version) return spec || '—'
                      return spec ? `${spec}（图纸 ${row.drawing_version}）` : `图纸 ${row.drawing_version}`
                    },
                  },
                  { title: '本次备注', dataIndex: 'remark', render: (v: string | null, row: SampleRequestRow['items'][number]) => <div>{v || '—'}{row.source_snapshot && (v || '') !== (row.source_snapshot.remark || '') && <div style={{ fontSize: 12 }}>原备注：{row.source_snapshot.remark || '未记录'}</div>}</div> },
                  { title: '本次规格', dataIndex: 'specification', render: (v: string | null, row: SampleRequestRow['items'][number]) => <div>{v || '—'}{row.source_snapshot && v !== row.source_snapshot.specification && <div style={{ fontSize: 12 }}>原规格：{row.source_snapshot.specification || '未记录'}</div>}</div> },
                  // 已寄样/已签收不给改：后端也会拒，这里不显示入口省得点了才报错
                  ...(canManage && !['shipped', 'signed'].includes(detail.status)
                    ? [
                        {
                          title: '操作',
                          width: 64,
                          render: (_: unknown, row: SampleItem) => (
                            <a
                              onClick={() =>
                                setItemEdit({
                                  id: row.id,
                                  craft: row.craft ?? '',
                                  material: row.material ?? '',
                                  drawing_version: row.drawing_version ?? '',
                                })
                              }
                            >
                              改依据
                            </a>
                          ),
                        },
                      ]
                    : []),
                ]}
              />
              {canManage && !['shipped', 'signed'].includes(detail.status) && (
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <Select
                    style={{ width: 92 }}
                    value={newItem.mode}
                    onChange={(value) =>
                      setNewItem({
                        mode: value as ItemDraft['mode'],
                        quantity: newItem.quantity,
                      })
                    }
                    optionList={[
                      { value: 'sku', label: '现货 SKU' },
                      { value: 'custom', label: '定制需求' },
                    ]}
                  />
                  {newItem.mode === 'custom' ? (
                    <Select
                      style={{ flex: 1 }}
                      placeholder="选择定制需求（编号）"
                      filter={optionMatcher}
                      value={newItem.inquiry_id}
                      onChange={(value) => setNewItem({ ...newItem, inquiry_id: value as number })}
                      optionList={inquiryOptions}
                    />
                  ) : (
                    <Select
                      style={{ flex: 1 }}
                      placeholder="追加 SKU"
                      filter={optionMatcher}
                      value={newItem.sku_id}
                      onChange={(value) => setNewItem({ ...newItem, sku_id: value as number })}
                      optionList={skuOptions}
                    />
                  )}
                  <Input
                    style={{ width: 80 }}
                    value={newItem.quantity}
                    onChange={(value) => setNewItem({ ...newItem, quantity: value })}
                  />
                  <Button
                    disabled={!draftReady(newItem)}
                    loading={addItemMutation.isPending}
                    onClick={() => addItemMutation.mutate()}
                  >
                    添加
                  </Button>
                </div>
              )}
            </div>

            {detail.shipments.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>寄样记录</div>
                <Table
                  size="small"
                  rowKey="id"
                  pagination={false}
                  dataSource={detail.shipments}
                  columns={[
                    { title: '承运商', dataIndex: 'carrier', render: (v: string | null) => v ?? '-' },
                    {
                      title: '快递单号',
                      dataIndex: 'tracking_no',
                      render: (v: string | null) => v ?? '-',
                    },
                    {
                      title: '运费',
                      dataIndex: 'shipping_fee',
                      width: 90,
                      render: (v: number) => `¥${v.toFixed(2)}`,
                    },
                  ]}
                />
              </div>
            )}

            {canManage && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {/* 待审批时是「批准 / 拒绝」；已驳回时给两条纠错的路 ——
                    主管改判（上次驳错了）、跟单原样重提（认为驳回理由不成立）。
                    这两条是不同角色在办事，缺哪条都会卡住那一半人。 */}
                {(detail.status === 'pending' || detail.status === 'rejected') && (
                  <>
                    {detail.status === 'pending' && (
                      <Input
                        placeholder="拒绝原因（拒绝时必填）"
                        value={rejectReason}
                        onChange={setRejectReason}
                      />
                    )}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <Button
                        theme="solid"
                        type="primary"
                        loading={approveMutation.isPending}
                        onClick={() => approveMutation.mutate(true)}
                      >
                        {detail.status === 'rejected' ? '改判为批准' : '批准'}
                      </Button>
                      {detail.status === 'pending' && (
                        <Button
                          type="danger"
                          loading={approveMutation.isPending}
                          onClick={() => approveMutation.mutate(false)}
                        >
                          拒绝
                        </Button>
                      )}
                      {detail.status === 'rejected' && (
                        <Button
                          loading={resubmitMutation.isPending}
                          onClick={() => resubmitMutation.mutate()}
                        >
                          重新提交审批
                        </Button>
                      )}
                    </div>
                    {detail.status === 'rejected' && (
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, lineHeight: 1.6 }}>
                        「改判为批准」是主管纠正误驳回；「重新提交审批」是跟单认为驳回理由不成立、
                        一个字不改原样再报一次。改了资料会自动回到「待审批」，不必点这两个。
                      </div>
                    )}
                  </>
                )}

                {detail.status === 'approved' && (
                  <>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <Input
                        placeholder="承运商"
                        value={shipForm.carrier}
                        onChange={(value) => setShipForm({ ...shipForm, carrier: value })}
                      />
                      <Input
                        placeholder="快递单号"
                        value={shipForm.tracking_no}
                        onChange={(value) => setShipForm({ ...shipForm, tracking_no: value })}
                      />
                      <Input
                        style={{ width: 100 }}
                        placeholder="运费"
                        value={shipForm.shipping_fee}
                        onChange={(value) => setShipForm({ ...shipForm, shipping_fee: value })}
                      />
                    </div>
                    <Button
                      theme="solid"
                      type="primary"
                      loading={shipMutation.isPending}
                      onClick={() => shipMutation.mutate()}
                    >
                      登记寄样
                    </Button>
                  </>
                )}

                {detail.status === 'shipped' && (
                  <Button
                    theme="solid"
                    type="primary"
                    loading={signMutation.isPending}
                    onClick={() => signMutation.mutate()}
                  >
                    登记签收
                  </Button>
                )}

                {['shipped', 'signed'].includes(detail.status) && (
                  <>
                    <TextArea
                      placeholder="客户反馈"
                      value={feedback}
                      onChange={setFeedback}
                      rows={2}
                    />
                    <Button
                      disabled={!feedback.trim()}
                      loading={feedbackMutation.isPending}
                      onClick={() => feedbackMutation.mutate()}
                    >
                      登记反馈
                    </Button>
                  </>
                )}

                {detail.feedback && (
                  <div style={{ background: 'var(--crm-surface-low)', padding: 12, borderRadius: 4 }}>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>客户反馈</div>
                    <div>{detail.feedback}</div>
                  </div>
                )}

                {detail.source_context && <p>打样来源：{detail.source_context.no} V{detail.source_context.version}{detail.source_context.is_historical ? '（取用时为历史版本）' : ''}</p>}
                {/* 生产打样资料（文档 §3.5）：车间照着这张单子干活——
                    这里放的是**整单属性**（用途 / 交期 / 验收标准 / 费用）；
                    材质、工艺、图纸版本逐行不同，在上面的明细行里改 */}
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                    <div style={{ fontWeight: 600, flex: 1 }}>生产打样资料</div>
                    {canManage && !prodEditing && (
                      <a
                        onClick={() => {
                          setProdForm({
                            purpose: detail.purpose ?? '',
                            target_completion_date: detail.target_completion_date ?? '',
                            acceptance_criteria: detail.acceptance_criteria ?? '',
                            sample_fee:
                              detail.sample_fee != null ? String(detail.sample_fee) : '',
                          })
                          setProdEditing(true)
                        }}
                      >
                        编辑
                      </a>
                    )}
                  </div>

                  {prodEditing ? (
                    <div style={{ display: 'grid', gap: 8 }}>
                      <Input
                        placeholder="用途（如：客户新品打样确认）"
                        value={prodForm.purpose}
                        onChange={(v) => setProdForm({ ...prodForm, purpose: v })}
                      />
                      <div style={{ display: 'flex', gap: 8 }}>
                        <DatePicker
                          type="date"
                          format="yyyy-MM-dd"
                          showClear
                          style={{ flex: 1 }}
                          placeholder="选择目标完成日"
                          value={
                            prodForm.target_completion_date
                              ? new Date(prodForm.target_completion_date)
                              : undefined
                          }
                          onChange={(_, dateStr) =>
                            setProdForm({
                              ...prodForm,
                              target_completion_date: (dateStr as string) || '',
                            })
                          }
                        />
                        <Input
                          style={{ width: 120 }}
                          placeholder="费用（留空=未填）"
                          value={prodForm.sample_fee}
                          onChange={(v) => setProdForm({ ...prodForm, sample_fee: v })}
                        />
                      </div>
                      <TextArea
                        placeholder="验收标准"
                        rows={2}
                        value={prodForm.acceptance_criteria}
                        onChange={(v) => setProdForm({ ...prodForm, acceptance_criteria: v })}
                      />
                      <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                        材质、工艺、图纸版本在「样品明细」里逐行填——它们每个商品可能都不一样。
                        改这几项（或这里的交期、验收标准）会让已批准的单子退回「待审批」。
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Button
                          theme="solid"
                          type="primary"
                          loading={saveProdMutation.isPending}
                          onClick={() => saveProdMutation.mutate()}
                        >
                          保存
                        </Button>
                        <Button onClick={() => setProdEditing(false)}>取消</Button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 1.9 }}>
                      <div>用途：{dash(detail.purpose)}</div>
                      <div>目标完成日：{dash(detail.target_completion_date)}</div>
                      <div>验收标准：{dash(detail.acceptance_criteria)}</div>
                      <div>
                        打样费用：{detail.sample_fee != null ? `¥${detail.sample_fee}` : '未填'}
                      </div>
                      <div>制作完成：{dash(detail.made_at?.slice(0, 10))}</div>
                      {/* 制作依据（第一批返修 §3.5）：这次照哪几份文件做的。
                          三种情况必须分开说（返工单 P2-7）：
                            · 还没登记制作 → 不适用；
                            · 这条记录早于本功能 → **明确标注"未登记"**，
                              绝不能显示成"已确认没有依据"（那是把"不知道"说成了"没有"）；
                            · 登记过但当时没选 → 如实说"登记时未指定"。 */}
                      <div>
                        制作依据：
                        {!detail.made_at ? (
                          <span style={{ color: 'var(--crm-text-3)' }}>还没登记制作完成</span>
                        ) : !detail.basis_files ? (
                          <span style={{ color: 'var(--crm-warning)' }}>
                            这条记录早于「制作依据」功能，当时没有登记（不代表没有依据）
                          </span>
                        ) : detail.basis_files.length === 0 ? (
                          <span style={{ color: 'var(--crm-text-3)' }}>登记时未指定</span>
                        ) : (
                          detail.basis_files.map((file: BasisFile) => (
                            <div key={file.file_id} style={{ marginLeft: 8 }}>
                              · {file.file_name || `文件 #${file.file_id}`}
                              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                                （第 {file.sample_version ?? 1} 版依据，校验值{' '}
                                {file.checksum ? `${file.checksum.slice(0, 12)}…` : '未记录'}）
                              </span>
                            </div>
                          ))
                        )}
                      </div>
                      {detail.made_events && detail.made_events.length > 0 && (
                        <div>
                          制作记录：
                          {detail.made_events.map((event, index) => (
                            <div key={event.key ?? index} style={{ marginLeft: 8 }}>
                              · {event.at?.slice(0, 10) || '—'}
                              {event.note ? `：${event.note}` : ''}
                            </div>
                          ))}
                        </div>
                      )}
                      <div>
                        版本：第 {detail.version ?? 1} 版
                        {detail.superseded_by
                          ? `（已被新修订版 #${detail.superseded_by} 取代，只读）`
                          : ''}
                      </div>
                    </div>
                  )}

                  {canManage && detail.status === 'approved' && !detail.made_at && (
                    <Button
                      style={{ marginTop: 8 }}
                      onClick={() => {
                        setMadeForm({ remark: '', fileIds: [] })
                        setMadeModal(true)
                      }}
                    >
                      登记制作完成
                    </Button>
                  )}

                  {/* 已制作 / 已寄出之后改车间依据的**唯一出路**（§3.3）：
                      原地改会被后端拒绝（同一行上留着旧制作时间却写新资料，和已做出来的
                      实物对不上）。这里开新修订版：原版连同制作/寄送事实冻结保留。
                      已被取代的那一版不再给入口——它已经冻结只读。 */}
                  {canManage && !detail.superseded_by
                    && (detail.made_at || detail.status === 'shipped'
                        || detail.status === 'signed') && (
                    <Button
                      style={{ marginTop: 8, marginLeft: 8 }}
                      loading={reviseMutation.isPending}
                      onClick={() => reviseMutation.mutate()}
                    >
                      开新修订版（改车间依据）
                    </Button>
                  )}
                </div>

                {/* 客户确认：签收是物流事实、确认是业务事实，两者分开（文档 §3.5） */}
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>客户确认</div>
                  <div style={{ fontSize: 13, color: 'var(--crm-text-2)', marginBottom: 8 }}>
                    当前：{detail.confirm_status_label}
                    {detail.customer_confirmed_at
                      ? `（${detail.customer_confirmed_at.slice(0, 10)}）`
                      : ''}
                    ｜客户收到样品不等于样品被接受
                  </div>
                  {canManage && detail.status === 'signed' && detail.confirm_status === 'pending' && (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Input
                        placeholder="客户反馈（可不填）"
                        value={confirmRemark}
                        onChange={setConfirmRemark}
                      />
                      <Button
                        type="primary"
                        loading={confirmMutation.isPending}
                        onClick={() => confirmMutation.mutate(true)}
                      >
                        客户接受
                      </Button>
                      <Button
                        type="danger"
                        loading={confirmMutation.isPending}
                        onClick={() => confirmMutation.mutate(false)}
                      >
                        未通过
                      </Button>
                    </div>
                  )}
                </div>

                {/* 场景12：从这张打样申请出打样需求单，来源询价与本次差异随文件落快照 */}
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>打样需求单</div>
                  <BizDocPanel
                    docType="sample_request"
                    sampleRequestId={detail.id}
                    canManage={canManage}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </SideSheet>

      {/* 改一条明细的车间依据。单独开弹窗而不是行内编辑：这三个字段是「审批批的
          那一版资料」，改它们会触发退回重审，值得一次明确的确认动作 */}
      {/* 登记制作完成（含制作依据，第一批返修 §3.5 / 返工单 P2-7）。
          「照哪几份文件做的」必须在这里点一次：不点，事后出了质量问题连
          比对对象都没有；而"制作依据"这个字段后端早就存了，只是页面一直没接。 */}
      <Modal
        title="登记制作完成"
        visible={madeModal}
        onCancel={() => setMadeModal(false)}
        onOk={() => madeMutation.mutate()}
        confirmLoading={madeMutation.isPending}
        okText="登记"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-2)', marginBottom: 4 }}>
              制作说明（可选）
            </div>
            <TextArea
              rows={2}
              value={madeForm.remark}
              onChange={(value) => setMadeForm({ ...madeForm, remark: value })}
              placeholder="如：按第 2 版图纸做，色差已和客户确认"
            />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              这次照哪几份文件做的？
            </div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 6 }}>
              只能选「挂在这张打样单上的附件」（后端会校验）。选中的会连同文件校验值
              一起存成快照，事后可核对"就是这一份"。不选也能登记，但将来对不了账。
            </div>
            {madeFilesQuery.isLoading && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>正在读附件…</div>
            )}
            {!madeFilesQuery.isLoading && (madeFilesQuery.data ?? []).length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--crm-warning)' }}>
                这张单子上还没有附件。请先在详情的附件区上传图纸/确认件，再回来登记依据。
              </div>
            )}
            <div style={{ display: 'grid', gap: 4 }}>
              {(madeFilesQuery.data ?? []).map((file) => {
                const checked = madeForm.fileIds.includes(file.id)
                return (
                  <label
                    key={file.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '6px 8px',
                      border: `1px solid ${checked ? 'var(--crm-primary)' : 'var(--crm-outline)'}`,
                      borderRadius: 4,
                      cursor: 'pointer',
                      background: checked ? 'var(--crm-primary-soft)' : 'transparent',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setMadeForm({
                          ...madeForm,
                          fileIds: checked
                            ? madeForm.fileIds.filter((id) => id !== file.id)
                            : [...madeForm.fileIds, file.id],
                        })
                      }
                    />
                    <span style={{ fontSize: 13 }}>
                      {file.file_name}
                      {file.category ? `（${file.category}）` : ''}
                    </span>
                  </label>
                )
              })}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title="修改车间依据"
        visible={itemEdit !== null}
        onCancel={() => setItemEdit(null)}
        onOk={() => saveItemMutation.mutate()}
        confirmLoading={saveItemMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        {itemEdit && (
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input
                placeholder="材质"
                value={itemEdit.material}
                onChange={(v) => setItemEdit({ ...itemEdit, material: v })}
              />
              <Input
                placeholder="工艺"
                value={itemEdit.craft}
                onChange={(v) => setItemEdit({ ...itemEdit, craft: v })}
              />
            </div>
            <Input
              placeholder="图纸版本"
              value={itemEdit.drawing_version}
              onChange={(v) => setItemEdit({ ...itemEdit, drawing_version: v })}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              留空表示「未填」。这三项是车间干活的依据，改完已批准的单子会退回「待审批」，
              需要主管重新审批；已寄样 / 已签收的单子不能再改。
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
