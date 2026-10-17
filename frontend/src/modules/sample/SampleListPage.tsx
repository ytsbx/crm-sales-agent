import { formatBeijingTime, formatBeijingDate } from '../../shared/beijing-time'
import SampleDingTalkModal from './SampleDingTalkModal'
import FormLabel from '../../shared/components/FormLabel'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import {
  ConfirmRequiredModal,
  isConfirmRequired,
  confirmMessageOf,
} from '../../shared/components/ConfirmRequiredModal'
import { emptyText } from '../../shared/hooks/emptyText'
import {
  Button,
  DatePicker,
  Input,
  Modal,
  Popconfirm,
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
  deleteSample,
  feedbackSample,
  getSample,
  listSamples,
  madeSample,
  resubmitSample,
  restoreSample,
  reviseSample,
  shipSample,
  signSample,
  type BasisFile,
  type SampleItem,
  type SampleRequestRow,
  updateSample,
  updateSampleItem,
} from '../../shared/api/sample'
import { listBusinessFiles } from '../../shared/api/file'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import AttachmentPanel from '../common/AttachmentPanel'
import { usePermissions } from '../../shared/hooks/permissions'
import BizDocPanel from '../../shared/components/BizDocPanel'
import type { TagTone } from '../../shared/types'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
import GuardedButton from '../../shared/components/GuardedButton'

/** 样品（PRD §19 / 03-API §26）。 */

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  approved: 'blue',
  rejected: 'red',
  shipped: 'cyan',
  signed: 'green',
}

/**
 * 「已删除」这个筛选项**不是一个真的状态值**（状态里没有"已删除"）：
 * 选中它时不传 `status`，改传 `include_deleted=true`，由后端按
 * `deleted_at IS NOT NULL` 过滤出被软删的那些（2026-10-15 加软删时补）。
 *
 * 用一个一眼认得出的哨兵值，别拿 `'deleted'` 冒充状态 —— 那会和真状态混起来。
 */
const DELETED_STATUS_OPTION = '__deleted__'

const STATUS_OPTIONS = [
  { value: 'pending', label: '待审批' },
  { value: 'approved', label: '已批准' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'shipped', label: '已寄样' },
  { value: 'signed', label: '已签收' },
  { value: DELETED_STATUS_OPTION, label: '已删除' },
]

/**
 * 明细草稿。两条路径（场景09）：现货选 SKU；定制件尚无 SKU，选需求编号。
 * 定制件本来就要先打样再定 SKU，强制先建档等于把顺序反过来。
 */
type ItemDraft = {
  /** 选了产品库里的某个产品 → 关联 sku_id */
  sku_id?: number
  /** 选了定制需求 → 关联 inquiry_id（那条路还在，只是不再要用户先"选模式"） */
  inquiry_id?: number
  /** **手填的产品名称**：产品库和定制需求里都还没有它（新品往往打完样才建档）。
   *  2026-10-16 主人：申请单上就一格「产品名称」，没有"现货还是定制"这个区分。 */
  item_name?: string
  quantity: string
}

function draftReady(item: ItemDraft): boolean {
  // 三条路任一成立就能加：选到产品 / 选到定制需求 / **自己填了名字**
  return Boolean(item.sku_id ?? item.inquiry_id ?? item.item_name?.trim())
}

function draftPayload(item: ItemDraft): Record<string, unknown> {
  const quantity = Number(item.quantity) || 1
  if (item.sku_id) return { sku_id: item.sku_id, quantity }
  if (item.inquiry_id) {
    return {
      inquiry_id: item.inquiry_id,
      item_name: item.item_name?.trim() || null,
      quantity,
    }
  }
  // 只写了产品名称：后端 `_add_item` 有一条"只有名字"的路径（2026-10-16 加）
  return { item_name: item.item_name?.trim() || null, quantity }
}

function fmt(value?: string | null) {
  return value ? formatBeijingTime(value) : '-'
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
  const [oaTarget, setOaTarget] = useState<number | null>(null)
  const [createVisible, setCreateVisible] = useState(false)
  /** 打样申请的计时起点（场景18 操作耗时埋点） */
  const createStartedAt = useRef<number | null>(null)
  const [form, setForm] = useState<{
    opportunity_id?: number
    customer_id?: number
    remark: string
    items: ItemDraft[]
  }>({ remark: '', items: [{ quantity: '1' }] })
  const selectedOpportunityQuery = useQuery({
    queryKey: ['opportunity', form.opportunity_id],
    queryFn: () => getOpportunity(form.opportunity_id!),
    enabled: createVisible && Boolean(form.opportunity_id),
  })

  useEffect(() => {
    if (!createOpportunityId || !createOpportunityQuery.data || !canManage) return
    setForm({ opportunity_id: createOpportunityId, customer_id: createOpportunityQuery.data.customer_id,
      remark: '', items: [{ quantity: '1' }] })
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
  const [newItem, setNewItem] = useState<ItemDraft>({ quantity: '1' })
  // 生产打样资料（文档 §3.5）：跟单在这一栏把车间要的东西补全，
  // 打样需求单出图时逐项带给车间。
  // 材质 / 工艺 / 图纸版本**不在这里**：它们逐行不同，在明细行上改（见 itemEdit）。
  // 打样积分＝预计工时 × 10（钉钉表上的口径），**不让手填** —— 手填必然出现
  // 两个人填出两个数。这里只存工时，积分由后端算、只读显示。
  const [prodForm, setProdForm] = useState({
    purpose: '',
    target_completion_date: '',
    acceptance_criteria: '',
    sample_fee: '',
    // ---- 钉钉《打样申请单》上的格 ----
    sample_type: '',
    product_category: '',
    maker_unit: '',
    sample_purpose: '',
    urgency: '',
    sample_situation: '',
    estimated_hours: '',
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

  // 选了「已删除」就换一套参数：走 `include_deleted`，而不是拿它当状态传给后端
  const deletedOnly = status === DELETED_STATUS_OPTION
  const query = useQuery({
    queryKey: ['samples', { status, keyword, page, pageSize }],
    queryFn: () =>
      listSamples({
        status: deletedOnly ? undefined : status,
        include_deleted: deletedOnly || undefined,
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
    // 只列「进行中」的商机：已失单 / 已成交的不该再拿来开新的样品申请。
    // 项目里其它选商机的地方都带这道过滤（报价中心、报价列表、商机看板、工作台
    // 都是 `status: 'open'`），只有这里原先漏了 —— 于是已失单的商机也会出现在下拉里，
    // 跟同名的进行中商机长得一模一样（主人 2026-10-15 截图问「为什么两个一样的」）。
    queryFn: () => listOpportunities({ status: 'open', page: 1, page_size: 100 }),
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

  // 软删 / 恢复（2026-10-15）。两个动作都会改动列表，成功后统一 refresh 一遍：
  // 删掉的那条要从列表里消失，「已删除」里恢复的那条要回到正常列表。
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSample(id),
    onSuccess: () => {
      Toast.success('样品申请已删除，可在「已删除」里恢复')
      void refresh()
    },
    onError,
  })

  const restoreMutation = useMutation({
    mutationFn: (id: number) => restoreSample(id),
    onSuccess: () => {
      Toast.success('样品申请已恢复')
      void refresh()
    },
    onError,
  })

  const createMutation = useMutation({
    mutationFn: () => {
      if (!form.items.length || form.items.some((item) => !draftReady(item))) {
        throw new Error('请填写每一行的产品名称')
      }
      return createSample({
        use_dingtalk: true,
        opportunity_id: form.opportunity_id ?? null,
        customer_id: form.customer_id ?? null,
        remark: form.remark.trim() || null,
        items: form.items.map(draftPayload),
      })
    },
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
      Toast.success('打样草稿已创建，请选择钉钉流程并补充申请资料')
      setOaTarget(created.id)
      setCreateVisible(false)
      setForm({ remark: '', items: [{ quantity: '1' }] })
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
      // ⚠️ 运费必须**先校验再提交**（2026-10-14 审查）：原来直接 `Number(...)`，
      // 输入 `abc` 得到 `NaN`，经 axios/JSON.stringify 变成 `null` 打到
      // `shipping_fee: Decimal = Field(default=0, ge=0, ...)`（**非 Optional**），
      // 用户只会看到「参数校验失败：shipping_fee」，不知道该怎么改。
      // 与同文件 `saveProdMutation`（打样费用）同一口径，别再写第三个版本。
      shipSample(detailId!, (() => {
        const raw = shipForm.shipping_fee.trim()
        const fee = raw === '' ? 0 : Number(raw)
        if (!Number.isFinite(fee) || fee < 0) {
          throw new Error('运费要填一个不小于 0 的数字（留空按 0 算）')
        }
        if (raw !== '' && !/^\d+(\.\d{1,2})?$/.test(raw)) {
          throw new Error('运费最多两位小数')
        }
        return {
          carrier: shipForm.carrier.trim() || null,
          tracking_no: shipForm.tracking_no.trim() || null,
          shipping_fee: fee,
        }
      })()),
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
      // 预计工时：留空 = 未填；填了就必须是不小于 0 的数字
      const rawHours = prodForm.estimated_hours.trim()
      const hours = rawHours === '' ? null : Number(rawHours)
      if (hours !== null && (!Number.isFinite(hours) || hours < 0)) {
        throw new Error('预计工时要填一个不小于 0 的数字（留空表示「未填」）')
      }
      // 空串一律发 null：与"没填"同一口径（后端也按 null 处理）
      const blankToNull = (v: string) => (v.trim() ? v.trim() : null)
      return updateSample(detailId!, {
        purpose: prodForm.purpose || null,
        target_completion_date: prodForm.target_completion_date || null,
        acceptance_criteria: prodForm.acceptance_criteria || null,
        sample_fee: fee,
        // ---- 钉钉《打样申请单》上的格 ----
        sample_type: blankToNull(prodForm.sample_type),
        product_category: blankToNull(prodForm.product_category),
        maker_unit: blankToNull(prodForm.maker_unit),
        sample_purpose: blankToNull(prodForm.sample_purpose),
        urgency: blankToNull(prodForm.urgency),
        sample_situation: blankToNull(prodForm.sample_situation),
        estimated_hours: hours,
        // ⚠️ `product_name` 与那 6 项「打样具体要求」**刻意不在这里发**：
        // 它们已从本页表单移除（改到「钉钉打样申请」弹窗里填），若在这里发 null
        // 就会**把弹窗里填好的值擦掉**。PATCH 只改传了的字段，不发就保持原值。
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
  // 登记客户确认结果是**客户侧商务事实**，后端不带 confirm 返回 42206。
  // 「客户接受」与「未通过」都走这道确认（主人 2026-10-10 拍板：两个都加）。
  const [sampleGate, setSampleGate] = useState<{ message: string; accepted: boolean } | null>(
    null,
  )

  const confirmMutation = useMutation({
    mutationFn: ({ accepted, confirm }: { accepted: boolean; confirm: boolean }) =>
      confirmSample(detailId!, accepted, confirmRemark, confirm),
    onSuccess: (_row: SampleRequestRow, variables) => {
      Toast.success(variables.accepted ? '已登记：客户接受' : '已登记：客户未通过')
      setConfirmRemark('')
      void refresh()
    },
    onError: (error: Error, variables) => {
      if (isConfirmRequired(error)) {
        setSampleGate({ message: confirmMessageOf(error), accepted: variables.accepted })
      } else {
        onError(error)
      }
    },
  })

  const addItemMutation = useMutation({
    mutationFn: () => addSampleItem(detailId!, draftPayload(newItem)),
    onSuccess: (row: SampleRequestRow) => {
      // 往已批准 / 已驳回的单子里加明细也会触发重批：新明细带进来的材质工艺图纸版本
      // 同样是车间依据（后端走同一道闸门）。这里也要说清楚，别让用户以为状态自己变了。
      Toast.success(reopenedHint(detail?.status, row.status) ?? '明细已添加')
      setNewItem({ quantity: '1' })
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
      width: 140,
      render: (_: unknown, record: SampleRequestRow) => {
        // 已删除的行只给「恢复」：后端对已删的单子查详情一律 404，
        // 摆一个「详情」也是死的，点了只会报错。
        if (record.is_deleted) {
          // 恢复要走 `sample:manage`（与删除共用同一把钥匙），没权限就不摆按钮
          return canManage ? (
            <Popconfirm
              title="恢复这张打样单？恢复后会重新出现在列表里。"
              onConfirm={() => restoreMutation.mutate(record.id)}
            >
              <Button theme="borderless" size="small">
                恢复
              </Button>
            </Popconfirm>
          ) : null
        }
        return (
          <>
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
            {/* 能不能删由后端下发（`can_delete`）：只有还没走流程的（待审批 / 已驳回）
                才给「删除」—— 已批准之后的单子连着审批留痕与车间依据。判据别在前端再写一份。 */}
            {canManage && record.can_delete && (
              <Popconfirm
                title="删除后这张打样单不在列表里显示，可在「已删除」里恢复。"
                onConfirm={() => deleteMutation.mutate(record.id)}
              >
                <Button theme="borderless" size="small" type="danger">
                  删除
                </Button>
              </Popconfirm>
            )}
          </>
        )
      },
    },
  ]

  const skuOptions = (skusQuery.data ?? []).map((sku) => ({
    value: sku.id,
    // 下拉里带上 SKU 名称：只有 `编码 · 规格` 时，一个产品下几个 SKU 认不出区别
    label: [sku.sku_code, sku.name, sku.specification].filter(Boolean).join(' · '),
  }))
  const inquiryOptions = (inquiriesQuery.data?.items ?? []).map((row) => ({
    value: row.id,
    label: `${row.inquiry_no ?? `编号 ${row.id}`} · ${row.title}`,
  }))
  /**
   * 「产品名称」那**一个框**的全部候选项：产品库 + 定制需求。
   *
   * 为什么合成一个（2026-10-16 主人：「我不明白为什么还要选择这个」）：
   * 申请单原件上就一格「产品名称」，业务员不该先回答"我这是现货还是定制"。
   * 选项值用**类型区分**，不靠用户选模式：
   *   · `number`         = 产品库里的 SKU（`sku_id`）
   *   · `'custom:<id>'`  = 某条定制需求（`inquiry_id`）
   *   · 其它字符串        = 手填的新产品名（`item_name`，后端有"只有名字"的路径）
   * 定制需求那条**必须留着**：从定制需求发起打样时，后端靠它校验客户/商机归属一致。
   */
  const productChoices = [
    ...skuOptions,
    ...inquiryOptions.map((row) => ({ value: `custom:${row.value}`, label: `定制需求 · ${row.label}` })),
  ]

  useEffect(() => {
    if (detail && searchParams.get('oa') === '1') {
      setOaTarget(detail.id)
      setSearchParams((current) => { const next = new URLSearchParams(current); next.delete('oa'); return next }, { replace: true })
    }
  }, [detail, searchParams, setSearchParams])

  return (
    <div className="page-container">
      {oaTarget != null && <SampleDingTalkModal key={oaTarget} sampleId={oaTarget} onClose={() => { setOaTarget(null); void refresh() }} />}
      <ConfirmRequiredModal
        visible={sampleGate !== null}
        message={sampleGate?.message ?? ''}
        onCancel={() => setSampleGate(null)}
        onConfirm={() => {
          const accepted = sampleGate?.accepted
          setSampleGate(null)
          if (accepted !== undefined) {
            confirmMutation.mutate({ accepted, confirm: true })
          }
        }}
        confirmText="确认登记"
      />
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
        okText="保存草稿并填写钉钉申请"
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
              optionList={optionsWithCodeOnDuplicate(
                Array.from(new Map([
                  ...(opportunitiesQuery.data?.items ?? []),
                  ...(selectedOpportunityQuery.data ? [selectedOpportunityQuery.data] : []),
                ].map((opp) => [opp.id, opp])).values()),
                // 显示文案是「商机名 · 客户名」：同一个客户底下有同名商机时，
                // 两条会渲染成一模一样的文字，选哪条全靠猜。
                // 按项目规矩来 —— **撞车才补「编号 N」**（不撞车的仍然只显示文案，
                // 也不影响按编号搜：搜索走 optionMatcher，编号挂在 value 上）。
                (opp) => `${opp.title}${opp.customer_name ? ` · ${opp.customer_name}` : ''}`,
                (opp) => opp.id,
              )}
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
                  label: selectedOpportunityQuery.data.customer_name ?? `客户编号 ${selectedOpportunityQuery.data.customer_id}`,
                }] as const] : []),
              ]).values())}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>样品明细（产品名称 *，打样数量 *）</div>
            {form.items.map((item, index) => (
              <div key={index} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                {/* **一个框**（2026-10-16 主人：「我不明白为什么还要选择这个」）：
                    申请单原件上就一格「产品名称」，没有"现货还是定制"这个区分 ——
                    那个模式下拉是我们在把自己的数据结构推给业务员。
                    现在：能搜到产品库里的就选（仍关联 SKU）；搜不到就**直接打名字**
                    （当新品，后端有一条"只有名字"的路径，打完样才可能建档）。 */}
                <Select
                  style={{ flex: 1 }}
                  placeholder="产品名称：能搜到就选，搜不到就直接打字"
                  filter={optionMatcher}
                  allowCreate
                  // 允许清空重选；`allowCreate` 让"没搜到"的名字也能落下来
                  value={
                    item.sku_id
                      ? item.sku_id
                      : item.inquiry_id
                        ? `custom:${item.inquiry_id}`
                        : item.item_name
                          ? item.item_name
                          : undefined
                  }
                  onChange={(value) => {
                    const raw = value as number | string | undefined
                    // `custom:<id>` = 选了某条**定制需求**（见 `productChoices`）。
                    // 那条路保留：从定制需求发起打样时，后端靠它校验客户/商机归属一致。
                    const custom = typeof raw === 'string' && raw.startsWith('custom:')
                    const items = [...form.items]
                    items[index] = {
                      ...items[index],
                      // 数字 = 产品库里的某条 → 关联 SKU；`custom:` = 定制需求；其余字符串 = 手填的名字
                      sku_id: typeof raw === 'number' ? raw : undefined,
                      inquiry_id: custom ? Number(raw.slice(7)) : undefined,
                      item_name: typeof raw === 'string' && !custom ? raw : undefined,
                    }
                    setForm({ ...form, items })
                  }}
                  optionList={productChoices}
                />
                <Input
                  style={{ width: 96 }}
                  placeholder="打样数量"
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
                setForm({ ...form, items: [...form.items, { quantity: '1' }] })
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
        title={detail ? `样品申请编号 ${detail.id}` : '样品申请'}
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
              <div style={{ fontWeight: 600, marginBottom: 8 }}>样品明细（产品名称 *，打样数量 *）</div>
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
                    // 编码在上、名称在下（同报价明细的口径）
                    render: (v: string | null, row: SampleRequestRow['items'][number]) => (
                      <div>
                        <div>
                          {v ?? '-'}
                          {row.is_custom && (
                            <Tag size="small" style={{ marginLeft: 6 }}>
                              定制
                            </Tag>
                          )}
                        </div>
                        {row.sku_name && (
                          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            {row.sku_name}
                          </div>
                        )}
                      </div>
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
                  {/* 追加明细同样只用**一个框**（与上面新建时一致，
                      2026-10-16 主人：「我不明白为什么还要选择这个」） */}
                  <Select
                    style={{ flex: 1 }}
                    placeholder="产品名称：能搜到就选，搜不到就直接打字"
                    filter={optionMatcher}
                    allowCreate
                    value={
                      newItem.sku_id
                        ? newItem.sku_id
                        : newItem.inquiry_id
                          ? `custom:${newItem.inquiry_id}`
                          : newItem.item_name
                            ? newItem.item_name
                            : undefined
                    }
                    onChange={(value) => {
                      const raw = value as number | string | undefined
                      const custom = typeof raw === 'string' && raw.startsWith('custom:')
                      setNewItem({
                        quantity: newItem.quantity,
                        sku_id: typeof raw === 'number' ? raw : undefined,
                        inquiry_id: custom ? Number(raw.slice(7)) : undefined,
                        item_name: typeof raw === 'string' && !custom ? raw : undefined,
                      })
                    }}
                    optionList={productChoices}
                  />
                  <Input
                    style={{ width: 96 }}
                    placeholder="打样数量"
                    value={newItem.quantity}
                    onChange={(value) => setNewItem({ ...newItem, quantity: value })}
                  />
                  <GuardedButton
                    hintBelow
                    disabled={!draftReady(newItem)}
                    blockedReasons={['请先选一个产品，或直接把产品名称打进去。']}
                    action="添加明细"
                    loading={addItemMutation.isPending}
                    onClick={() => addItemMutation.mutate()}
                  >
  添加
</GuardedButton>
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

            {canManage && <Button theme="solid" onClick={() => setOaTarget(detail.id)}>钉钉打样申请 / 查看进度</Button>}
            {canManage && !((detail.source_context as unknown as { dingtalk_oa?: { submitted?: boolean } } | null)?.dingtalk_oa?.submitted) && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {/* 待审批时是「批准 / 拒绝」；已驳回时给两条纠错的路 ——
                    主管改判（上次驳错了）、跟单原样重提（认为驳回理由不成立）。
                    这两条是不同角色在办事，缺哪条都会卡住那一半人。 */}
                {(detail.status === 'pending' || detail.status === 'rejected') && !((detail.source_context as unknown as Record<string, unknown> | null)?.dingtalk_oa) && (
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
                            sample_type: detail.sample_type ?? '',
                            product_category: detail.product_category ?? '',
                            maker_unit: detail.maker_unit ?? '',
                            sample_purpose: detail.sample_purpose ?? '',
                            urgency: detail.urgency ?? '',
                            sample_situation: detail.sample_situation ?? '',
                            estimated_hours:
                              detail.estimated_hours != null ? String(detail.estimated_hours) : '',
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
                      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))' }}>
                        <div style={{ minWidth: 0 }}>
                          <FormLabel>目标完成日</FormLabel>
                          <DatePicker
                            type="date"
                            format="yyyy-MM-dd"
                            showClear
                            style={{ width: '100%' }}
                            placeholder="选择日期"
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
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <FormLabel>打样费用（元）</FormLabel>
                          <Input
                            style={{ width: '100%' }}
                            placeholder="请输入金额"
                            value={prodForm.sample_fee}
                            onChange={(v) => setProdForm({ ...prodForm, sample_fee: v })}
                          />
                          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--crm-text-3)' }}>
                            留空表示未填；免费请填 0
                          </div>
                        </div>
                      </div>
                      <TextArea
                        placeholder="验收标准"
                        rows={2}
                        value={prodForm.acceptance_criteria}
                        onChange={(v) => setProdForm({ ...prodForm, acceptance_criteria: v })}
                      />
                      {/* ---- 钉钉《打样申请单》上的格（2026-10-15）----
                          选项**照抄钉钉下拉**（钉钉那边是下拉，写错一个字导出的单子
                          就和钉钉对不上，而人眼发现不了）。填好这几项之后，
                          点下面的「导出打样申请单」就能拿到填好的 Excel，直接传钉钉。 */}
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样类型"
                          value={prodForm.sample_type || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_type: (v as string) ?? '' })}
                          optionList={[
                            { value: '设计研发内部打样', label: '设计研发内部打样' },
                            { value: '销售客户打样', label: '销售客户打样' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样产品大类"
                          value={prodForm.product_category || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, product_category: (v as string) ?? '' })}
                          optionList={[
                            { value: '高频类产品', label: '高频类产品' },
                            { value: '车缝类产品', label: '车缝类产品' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="样品制作单位"
                          value={prodForm.maker_unit || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, maker_unit: (v as string) ?? '' })}
                          optionList={[
                            { value: '义乌打样部', label: '义乌打样部' },
                            { value: '山东生产部', label: '山东生产部' },
                            { value: '外协厂', label: '外协厂' },
                          ]}
                        />
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <Select
                          style={{ width: 170 }}
                          placeholder="打样目的"
                          value={prodForm.sample_purpose || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_purpose: (v as string) ?? '' })}
                          optionList={[
                            { value: '客审确认样', label: '客审确认样' },
                            { value: '报价核算样', label: '报价核算样' },
                            { value: '定制改款样', label: '定制改款样' },
                            { value: '送检上架样', label: '送检上架样' },
                          ]}
                        />
                        <Select
                          style={{ width: 130 }}
                          placeholder="紧急程度"
                          value={prodForm.urgency || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, urgency: (v as string) ?? '' })}
                          optionList={[
                            { value: '普通', label: '普通' },
                            { value: '急', label: '急' },
                            { value: '紧急', label: '紧急' },
                          ]}
                        />
                        <Select
                          style={{ width: 170 }}
                          placeholder="样品情况"
                          value={prodForm.sample_situation || undefined}
                          onChange={(v) => setProdForm({ ...prodForm, sample_situation: (v as string) ?? '' })}
                          optionList={[
                            { value: '首次设计新品', label: '首次设计新品' },
                            { value: '设计更改', label: '设计更改' },
                            { value: '客户首次要求', label: '客户首次要求' },
                            { value: '客户更改', label: '客户更改' },
                            { value: '其他', label: '其他' },
                          ]}
                        />
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <Input
                          style={{ width: 160 }}
                          placeholder="预计工时（小时）"
                          value={prodForm.estimated_hours}
                          onChange={(v) => setProdForm({ ...prodForm, estimated_hours: v })}
                        />
                        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                          打样积分＝工时 × 10 =
                          <b>
                            {prodForm.estimated_hours.trim() &&
                            Number.isFinite(Number(prodForm.estimated_hours))
                              ? ` ${Number(prodForm.estimated_hours) * 10} `
                              : ' — '}
                          </b>
                          分（系统算，不手填）
                        </span>
                      </div>
                      {/* 2026-10-16：申请单上的**产品名称 + 打样具体要求 6 项**在这里
                          删掉了 —— 它们与「钉钉打样申请」弹窗里那一组是**同一批字段**
                          （弹窗从库里回填，两处读写同源），两套输入框只会让人不知道该填哪、
                          或者以为要填两遍。现在只在弹窗那一处填。
                          下面「用途 / 目标完成日 / 费用 / 验收标准」以及
                          「打样类型 / 产品大类 / 制作单位 / 预计工时」是**这里独有**的
                          （钉钉模板上没有对应格），所以留在本页。 */}
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
                      <div>制作完成：{dash(detail.made_at ? formatBeijingDate(detail.made_at) : null)}</div>
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
                              · {file.file_name || `文件编号 ${file.file_id}`}
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
                              · {event.at ? formatBeijingDate(event.at) : '—'}
                              {event.note ? `：${event.note}` : ''}
                            </div>
                          ))}
                        </div>
                      )}
                      <div>
                        版本：第 {detail.version ?? 1} 版
                        {detail.superseded_by
                          ? `（已被新修订版编号 ${detail.superseded_by} 取代，只读）`
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
                      ? `（${formatBeijingDate(detail.customer_confirmed_at)}）`
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
                        onClick={() => confirmMutation.mutate({ accepted: true, confirm: false })}
                      >
                        客户接受
                      </Button>
                      <Button
                        type="danger"
                        loading={confirmMutation.isPending}
                        onClick={() => confirmMutation.mutate({ accepted: false, confirm: false })}
                      >
                        未通过
                      </Button>
                    </div>
                  )}
                </div>

                {/* 附件区（2026-10-08 补）。
                    这里原本**没有上传入口**：而「登记制作完成」那个弹窗写着
                    「请先在详情的附件区上传图纸/确认件」—— 指向的正是这块不存在的地方，
                    于是「制作依据」永远只能选一个空列表，等于那个功能在界面上用不了。
                    后端一直是完整的：能上传、能登记依据（连文件校验值一起存快照）、
                    制作/寄出之后只许标「后续补充资料」。

                    锁定之后上传的**自动**按「后续补充资料」归类，不用用户选 ——
                    锁定与否由后端下发（`attachment_locked` / `attachment_lock`），
                    前端不自己按 made_at / status 推一遍（推出来的那份迟早和后端分叉）。 */}
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>附件</div>
                  {detail.attachment_locked && (
                    <div
                      style={{
                        marginBottom: 8,
                        fontSize: 12,
                        color: 'var(--crm-warning)',
                      }}
                    >
                      {detail.attachment_lock}：现在上传的附件会自动归到「后续补充资料」，
                      和制作依据分开记 —— 事后补进来的资料不能和「当时照哪份做的」混在一起。
                    </div>
                  )}
                  <AttachmentPanel
                    businessType="sample"
                    businessId={detail.id}
                    writePermission="sample:manage"
                    showCategory
                    category={detail.attachment_locked ? 'supplement' : undefined}
                  />
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
                这张单子上还没有附件。请先在上面的「附件」区上传图纸/确认件，再回来登记依据。
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
