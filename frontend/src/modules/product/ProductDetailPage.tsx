import EmptyState, { emptyTextNode } from '../../shared/components/EmptyState'
import { formatBeijingTime } from '../../shared/beijing-time'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Button,
  Checkbox,
  CheckboxGroup,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import {
  confirmLocalSkuMaster,
  confirmSkuMasterDiff,
  createSku,
  deleteProduct,
  disableSku,
  enableSku,
  getProduct,
  getSkuMaster,
  listSkuIdentitySources,
  replaySkuIdentity,
  renameSkuIdentity,
  stopSkuIdentity,
  setSkuFieldAuthority,
  listProductSkus,
  updateProduct,
  updateSku,
  type ProductPayload,
  type SkuMasterDiff,
  type SkuMasterField,
  type SkuIdentitySource,
  type SkuPayload,
} from '../../shared/api/product'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import HelpTip from '../../shared/components/HelpTip'
import { useNotePrompt } from '../../shared/components/NotePromptModal'
import AttachmentPanel from '../common/AttachmentPanel'
import ImageGallery from '../../shared/components/ImageGallery'
import SkuImageCell from './SkuImageCell'
import PendingImagePicker from './PendingImagePicker'
import { listBusinessFilesBatch, uploadFile, type FileRow } from '../../shared/api/file'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'
import type { Sku } from '../../shared/types'
import FormLabel from '../../shared/components/FormLabel'
import GuardedButton from '../../shared/components/GuardedButton'

/**
 * 主数据差异的中文口径与核定结论。
 *
 * 为什么在前端再放一份文案：后端返回的是稳定取值（keep_local 这类），
 * 页面要给业务同事看中文。允许的结论**不由前端决定** —— 用的是后端随差异下发的
 * `allowed_resolutions`，所以"同名不同码不能选自动合并"这类规则只在一个地方维护。
 */
const DIFF_TYPE_LABELS: Record<string, string> = {
  unit_conflict: '单位冲突',
  package_conflict: '包装冲突',
  field_conflict: '字段不一致',
  confirmed_value_differs: '与已确认版本不一致',
  null_overwrite: '来源上报了空值',
  code_rename: '来源改码',
  stopped_source: '来源标记停用',
  same_name_diff_code: '同名不同码',
  unmatched_sku_source: '本地还没有这条 SKU',
}

const RESOLUTION_LABELS: Record<string, string> = {
  keep_local: '以本地为准',
  take_external: '以来源为准',
  manual: '人工已处理',
  ignore: '不是差异',
  rename_local: '按来源新编码改本地编码',
  disable_local: '按来源停用本地 SKU',
}

const FIELD_STATUS_LABELS: Record<string, { text: string; color: 'grey' | 'orange' | 'green' | 'red' }> = {
  unverified: { text: '待核实', color: 'grey' },
  pending_confirmation: { text: '待确认', color: 'orange' },
  confirmed: { text: '已确认', color: 'green' },
  conflict: { text: '有冲突', color: 'red' },
}

/** SKU 表单用字符串保存，提交时再转数字——避免半成品输入被强转成 NaN。 */
interface SkuForm {
  sku_code: string
  /** SKU 名称：选填。规格是参数（60L 600×400×400mm），名称是给人看的一句话。 */
  name: string
  specification: string
  color: string
  material: string
  length: string
  width: string
  height: string
  weight: string
  carton_qty: string
  carton_volume: string
  moq: string
  package_type: string
  unit: string
}

const EMPTY_SKU: SkuForm = {
  sku_code: '',
  name: '',
  specification: '',
  color: '',
  material: '',
  length: '',
  width: '',
  height: '',
  weight: '',
  carton_qty: '',
  carton_volume: '',
  moq: '',
  package_type: '',
  unit: '件',
}

/** Q6 决策3：数值字段提交**原样字符串**（精确留档），不再经 JS Number 读回丢分。
    空串→null；非法文本交后端 parse_numeric_field 明确报错（旧 toNumber 会静默变 null 抹数据）。 */
const toExactNumeric = (value: string): string | null => {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

/** 正式报价对客要印的三个字段，与后端 `master.QUOTE_DISPLAY_FIELDS` 对齐。 */
const QUOTE_FIELD_NAMES: readonly string[] = ['name', 'specification', 'unit']

/**
 * 参与**运费计算**的字段（主人 2026-10-10 要求标注影响面）。
 *
 * 为什么单独列出来：这几个字段确认与否不影响能不能发送，但会影响运费试算的
 * 结果 —— 不标注的话，用户不知道该不该顺手确认它们。
 * 与后端 `master.MASTER_FIELDS` 同一份口径，只挑与计费相关的。
 */
const FREIGHT_FIELD_NAMES: readonly string[] = [
  'weight',
  'length',
  'width',
  'height',
  'carton_qty',
  'carton_volume',
]

export default function ProductDetailPage() {
  /* 三处原来都是 window.prompt（改来源编码、停用来源、填权威来源）：
     浏览器原生弹窗没法做必填、也不能解释"改的是哪条来源"。 */
  const notePrompt = useNotePrompt()
  const params = useParams()
  const navigate = useNavigate()
  const productId = Number(params.id)
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('product:manage')

  const [editVisible, setEditVisible] = useState(false)
  const [productForm, setProductForm] = useState<ProductPayload>({ name: '' })
  const [skuVisible, setSkuVisible] = useState(false)
  //: 正在给哪个 SKU 管图片（null = 弹窗关闭）。产品/SKU 两级都要图片，
  //: 产品那一节常驻在页面里，SKU 这一级用弹窗——SKU 可能有很多行，
  //: 每行都铺一块图片墙会把列表撑得没法看。
  const [imageSku, setImageSku] = useState<Sku | null>(null)
  //: **新建 SKU 时暂存的图片**：此刻 SKU 还没有 id，没法直接挂，
  //: 所以先把 File 留在内存里，点保存时"先建 SKU、拿到 id 再逐张上传"。
  const [pendingImages, setPendingImages] = useState<File[]>([])
  const [editingSku, setEditingSku] = useState<Sku | null>(null)
  const [skuForm, setSkuForm] = useState<SkuForm>(EMPTY_SKU)
  /** 正在看"来源/待核实/差异"的 SKU（null = 没打开面板）。 */
  const [masterSku, setMasterSku] = useState<Sku | null>(null)
  // 勾选要确认的字段：默认对客三字段（它们影响正式发送）。
  // 每次打开弹窗都重置为默认，避免上一次的勾选残留造成误确认。
  const [confirmFields, setConfirmFields] = useState<string[]>([...QUOTE_FIELD_NAMES])
  const [resolutions, setResolutions] = useState<Record<number, string>>({})
  const [notes, setNotes] = useState<Record<number, string>>({})

  const productQuery = useQuery({
    queryKey: ['product', productId],
    queryFn: () => getProduct(productId),
    enabled: Number.isFinite(productId),
  })
  // 页签标题用产品名，而不是所有产品都叫「产品详情」
  useTabLabel(productQuery.data?.name)
  const skuQuery = useQuery({
    queryKey: ['product-skus', productId],
    queryFn: () => listProductSkus(productId),
    enabled: Number.isFinite(productId),
  })

  // SKU 列表要显示缩略图。**用批量接口一次取**：逐行调就是 N+1，
  // 一个产品有几个型号就发几个请求（见 `listBusinessFilesBatch` 的注释）。
  const skuIds = (skuQuery.data ?? []).map((s) => s.id)
  const skuImagesQuery = useQuery({
    queryKey: ['sku-images', skuIds.join(',')],
    queryFn: () => listBusinessFilesBatch('sku', skuIds),
    enabled: skuIds.length > 0,
  })
  //: sku_id -> 图片附件（只留 mime 是 image/ 的；资料类不算"图片"）
  const skuImages = useMemo(() => {
    const out: Record<number, FileRow[]> = {}
    for (const [key, rows] of Object.entries(skuImagesQuery.data ?? {})) {
      const imgs = (rows ?? []).filter((r) => (r.mime_type ?? '').toLowerCase().startsWith('image/'))
      if (imgs.length > 0) out[Number(key)] = imgs
    }
    return out
  }, [skuImagesQuery.data])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['product', productId] })
    void queryClient.invalidateQueries({ queryKey: ['product-skus', productId] })
    void queryClient.invalidateQueries({ queryKey: ['products'] })
  }

  const updateMutation = useMutation({
    mutationFn: (payload: ProductPayload) => updateProduct(productId, payload),
    onSuccess: () => {
      Toast.success('产品资料已保存')
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const skuMutation = useMutation({
    mutationFn: async (payload: SkuPayload) => {
      if (editingSku) {
        // 编辑态不碰图片：SKU 已有 id，点列表里的图片格直接传，不必绕暂存
        await updateSku(editingSku.id, payload)
        return { skuId: editingSku.id, uploaded: 0, failed: [] as string[] }
      }
      const created = await createSku(productId, payload)
      const skuId = (created as { id?: number })?.id
      if (!skuId) {
        throw new Error('SKU 已创建，但接口没返回 id，图片未能上传')
      }
      // 图片逐张上传。**一张失败不影响其它张**：把失败的名字收集起来，最后一起提示，
      // 并把 SKU 建成的结果保住（不因为图片失败把它回滚成"没创建"）。
      const failed: string[] = []
      for (const file of pendingImages) {
        try {
          await uploadFile(file, { businessType: 'sku', businessId: skuId })
        } catch {
          failed.push(file.name)
        }
      }
      return { skuId, uploaded: pendingImages.length - failed.length, failed }
    },
    onSuccess: (res) => {
      if (editingSku) {
        Toast.success('SKU 已保存')
      } else if (res.failed.length > 0) {
        Toast.warning(`SKU 已创建；有 ${res.failed.length} 张图片没传上（${res.failed.join('、')}），可在列表「图片」列补传`)
      } else if (res.uploaded > 0) {
        Toast.success(`SKU 已创建，${res.uploaded} 张图片已上传`)
      } else {
        Toast.success('SKU 已创建')
      }
      setSkuVisible(false)
      setEditingSku(null)
      setSkuForm(EMPTY_SKU)
      setPendingImages([])
      refresh()
      // 新 SKU 的图片要立刻显示：批量查询的 key 里含 skuIds，列表刷新后
      // key 变了会自然重取；这里再显式失效一次，避免时序上先渲染旧 key。
      void queryClient.invalidateQueries({ queryKey: ['sku-images'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      enable ? enableSku(id) : disableSku(id),
    onSuccess: () => {
      Toast.success('已更新 SKU 状态')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteProduct(productId),
    onSuccess: () => {
      Toast.success('产品已删除')
      void queryClient.invalidateQueries({ queryKey: ['products'] })
      navigate('/products')
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 主数据来源/差异只在**打开面板时**才拉：按 SKU 逐个预取会给列表页带来
   * 一堆没人看的请求，而这套数据（来源、确认版本、差异）本来就是按需查看的。
   */
  const masterQuery = useQuery({
    queryKey: ['sku-master', masterSku?.id],
    queryFn: () => getSkuMaster(masterSku!.id),
    enabled: masterSku !== null,
  })
  const identityQuery = useQuery({
    queryKey: ['sku-identities', masterSku?.id],
    queryFn: () => listSkuIdentitySources(masterSku!.id),
    enabled: masterSku !== null,
  })
  const identityActionMutation = useMutation({
    mutationFn: async ({ action, source }: { action: 'replay' | 'rename' | 'stop'; source: SkuIdentitySource }) => {
      if (action === 'replay') return replaySkuIdentity(source.id)
      if (action === 'rename') {
        const next = await notePrompt.ask({
          title: '修改来源编码',
          label: '新编码',
          description: `改的是「${source.system_type}」这条台账的编码（当前：${source.external_code}），改完按新编码对齐主数据。`,
          defaultValue: source.external_code,
          required: true,
          okText: '保存',
        })
        if (!next) throw new Error('未填写新编码')
        return renameSkuIdentity(source.id, next)
      }
      const note = await notePrompt.ask({
        title: '停用这个编码来源',
        label: '停用说明',
        description: '停用后这条来源不再参与编码对齐，历史记录保留；之后想恢复要重新登记。',
        placeholder: '例如：对方已更换编码规则，改用新供应商编码',
        required: false,
        tone: 'warning',
        okText: '停用',
      })
      if (note === null) return { cancelled: true } as const
      return stopSkuIdentity(source.id, note)
    },
    onSuccess: (data) => {
      if (data && typeof data === 'object' && 'cancelled' in data && data.cancelled === true) return
      Toast.success('来源台账已更新')
      void queryClient.invalidateQueries({ queryKey: ['sku-identities', masterSku?.id] })
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const authorityMutation = useMutation({
    mutationFn: ({ fieldName, authority }: { fieldName: string; authority: string | null }) =>
      setSkuFieldAuthority({ sku_id: masterSku!.id, field_name: fieldName, authority }),
    onSuccess: () => {
      Toast.success('字段权威归属已保存')
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const confirmDiffMutation = useMutation({
    mutationFn: ({ diffId, resolution, note }: { diffId: number; resolution: string; note?: string }) =>
      confirmSkuMasterDiff(diffId, { resolution, note }),
    onSuccess: (data) => {
      Toast.success(data.message)
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
      void queryClient.invalidateQueries({ queryKey: ['product-skus', productId] })
    },
  })

  /**
   * 正式报价对客要印的三个字段（与后端 `QUOTE_DISPLAY_FIELDS` 对齐）。
   *
   * "确认过"的判据是 `confirmed_version > 0` —— 与后端闸门
   * `require_confirmed_master` **同一个判据**，不是另写一套。
   * 注意：值可以是空串（例如没有规格的产品），那是**合法的已确认值**，
   * 所以判据是"确认版本号大于 0"，不是"值非空"。
   */
  const quoteFieldsMissingLabels = (masterQuery.data?.fields ?? [])
    .filter((f) => QUOTE_FIELD_NAMES.includes(f.field_name) && !(f.confirmed_version > 0))
    .map((f) => f.field_label)
  // ⚠️ 必须是"**三个都**确认过"才算齐 —— 用 `.some()` 写成了"任一已确认"，
  // 于是"只确认了名称、规格和单位还没确认"时按钮不显示，用户仍然卡住、
  // 而这正是最容易出现也最难解释的半截状态（后端闸门要三个都齐）。
  // 后端闸门也是"缺任何一个就拒"，两边判据必须一致。
  const quoteFieldsConfirmed = masterQuery.data
    ? QUOTE_FIELD_NAMES.every((name) =>
        (masterQuery.data.fields ?? []).some(
          (f) => f.field_name === name && f.confirmed_version > 0,
        ),
      )
    : true

  // 本地改了、还没重新确认的字段（后端算好 `local_differs_from_confirmed`，
  // 前端不自己比 —— 判据只写一处）
  const changedSinceConfirm = (masterQuery.data?.fields ?? [])
    .filter((f) => f.local_differs_from_confirmed)
    .map((f) => f.field_label)

  const confirmLocalMutation = useMutation({
    mutationFn: ({ skuId, fields }: { skuId: number; fields: string[] }) =>
      confirmLocalSkuMaster(skuId, { fields, note: '产品详情页本地核对确认' }),
    onSuccess: (data) => {
      Toast.success(data.message)
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
      void queryClient.invalidateQueries({ queryKey: ['product-skus', productId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const product = productQuery.data
  if (productQuery.isLoading) return <div className="page-container">加载中…</div>
  /* 原来这里是一行裸字「产品不存在」，占着整页白底 —— 链接失效/产品被删时
     用户会以为页面崩了（2026-11-20 巡检）。改成统一空态：图标 + 说清原因 + 回列表。 */
  if (!product) {
    return (
      <div className="page-container">
        <EmptyState
          title="产品不存在或已下线"
          hint="可能是链接里的产品编号有误，或这条产品已从产品库移除；回产品列表重新选择即可。"
        />
        <div style={{ display: 'flex', justifyContent: 'center', paddingBottom: 24 }}>
          <Button type="primary" theme="solid" onClick={() => navigate('/products')}>返回产品列表</Button>
        </div>
      </div>
    )
  }

  /**
   * 字段级"来源 / 来源状态 / 更新时间 / 权威归属 / 确认版本"。
   *
   * 列宽合计必须 ≤ 弹窗内容区宽度（MASTER_MODAL_WIDTH 减去左右内边距）。
   * 原来合计 910px，而 920 宽的弹窗扣掉内边距只剩约 872px —— 表格放不下，
   * 最后一列「状态」被挤到可视区之外，表头也被压成两行。
   * 现在按"表头文字宽 + 内边距"逐列量过。**每次加列都要同步
   * `MASTER_TABLE_WIDTH`**，否则最后一列会被挤出可视区（历史上踩过一次）。
   */
  const masterFieldColumns = [
    { title: '字段', dataIndex: 'field_label', width: 110, ellipsis: true },
    {
      title: '本地值',
      width: 130,
      ellipsis: true,
      render: (_: unknown, record: SkuMasterField) => record.local_value ?? '-',
    },
    {
      title: '来源',
      width: 100,
      ellipsis: true,
      render: (_: unknown, record: SkuMasterField) => record.source_system ?? '-',
    },
    {
      title: (
        <span className="crm-help-label">
          来源状态
          <HelpTip forLabel="来源状态" text="已核实 = 外部来源的值已人工确认；待核实 = 有来源但还没确认；无外部来源 = 本地自建，不用等外部核对。" />
        </span>
      ),
      width: 92,
      // 三值：已核实 / 待核实（有来源但没核实）/ 无外部来源（本地自建）。
      // 「无外部来源」从前被写成「待核实」，让人以为还差一步外部核对 —— 而它永远等不到。
      render: (_: unknown, record: SkuMasterField) =>
        record.source_status === '已核实' ? (
          <Tag color="green">已核实</Tag>
        ) : record.source_status === '无外部来源' ? (
          <Tag color="grey">无外部来源</Tag>
        ) : (
          <Tag color="orange">待核实</Tag>
        ),
    },
    {
      title: '来源更新时间',
      width: 150,
      render: (_: unknown, record: SkuMasterField) =>
        record.source_updated_at ? formatBeijingTime(record.source_updated_at) : '-',
    },
    {
      title: (
        <span className="crm-help-label">
          权威归属
          <HelpTip forLabel="权威归属" text="这个字段以哪个系统为准。空着表示还没拍板，暂时按当前值使用。" />
        </span>
      ),
      width: 150,
      render: (_: unknown, record: SkuMasterField) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span>{record.authority_label}</span>
          {canManage && (
            <Button
              size="small"
              theme="borderless"
              onClick={async () => {
                const next = await notePrompt.ask({
                  title: '填写权威来源',
                  label: '权威来源',
                  description: '这个字段以谁的值为准（例如「供应商官网」「采购确认单」）；留空表示还没拍板。',
                  defaultValue: record.authority ?? record.source_system ?? '',
                  required: false,
                  maxLength: 60,
                  okText: '保存',
                })
                if (next !== null) authorityMutation.mutate({ fieldName: record.field_name, authority: next || null })
              }}
            >
              设置
            </Button>
          )}
        </span>
      ),
    },
    {
      title: '上次确认值',
      width: 130,
      ellipsis: true,
      render: (_: unknown, record: SkuMasterField) =>
        record.confirmed_version > 0 ? (
          <span
            style={{
              color: record.local_differs_from_confirmed
                ? 'var(--semi-color-warning)'
                : undefined,
            }}
          >
            {record.confirmed_value ?? '（空）'}
            {record.local_differs_from_confirmed && ' ← 本地已改'}
          </span>
        ) : (
          '-'
        ),
    },
    {
      title: '确认版本',
      width: 92,
      render: (_: unknown, record: SkuMasterField) =>
        record.confirmed_version > 0 ? `v${record.confirmed_version}` : '-',
    },
    {
      title: '状态',
      width: 96,
      render: (_: unknown, record: SkuMasterField) => {
        const meta = FIELD_STATUS_LABELS[record.status] ?? {
          text: record.status,
          color: 'grey' as const,
        }
        return <Tag color={meta.color}>{meta.text}</Tag>
      },
    },
  ]
  /** 表格列宽合计：与上面 8 列 widths 之和保持一致，改列宽时同步改这里。 */
  // 2026-10-10 新增「上次确认值」列（130）→ 合计 1000。
  // 每次改列都要同步这里，否则最后一列会被挤出可视区（历史上踩过）。
  const MASTER_TABLE_WIDTH = 1000
  /** 弹窗外宽；窄屏（笔记本、分屏）由 maxWidth 兜住，不会顶出屏幕。 */
  const MASTER_MODAL_WIDTH = 1200

  const skuColumns = [
    {
      // 单开一列（主人 2026-10-10："单开一列，不要横向滚动"）。
      // 宽度是**算出来的**：原来 13 列合计 1595px，1600 窗口下可用只有 1294px，
      // 即"正常窗口下本来就在横滚"。所以这一列的位置靠合并冗余列 + 压窄换出来
      // （详见下面「规格·尺寸·重量」那一列的注释）。
      title: '名称 / 编码',
      dataIndex: 'name',
      width: 140,
      ellipsis: true,
      render: (v: string | null, record: Sku) => (
        <div>
          <div>{v || product.name}</div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }} title={record.sku_code}>{record.sku_code}</div>
        </div>
      ),
    },
    {
      // 图片就放在 SKU 列表这儿（主人 2026-10-10 口径）：直接看到图，
      // 点一下开图片墙（看全部 / 上传 / 删除）。不再单独开"产品图片"区块。
      title: '图片',
      // 66 的时候格子塞不下（40 的方框 + 6 间距 + 两侧内边距 = 78），
      // 表格给这一格挂了个省略号，看着像"+ 后面还有个坏掉的按钮"（2026-11-20）。
      // 加到 84，一格里正好放一个缩略图；多图时张数跟在后面也不挤。
      width: 84,
      render: (_: unknown, record: Sku) => (
        <SkuImageCell
          images={skuImages[record.id] ?? []}
          canWrite={canManage}
          onClick={() => setImageSku(record)}
        />
      ),
    },
    // 原来这里是「规格」「尺寸」「重量」**三列**，合计 480px。
    // 合并成一列省下 200px —— 这是给「SKU 名称」腾位置的主要来源。
    // 合并**不丢信息**，而且内容本来就在重复：实测产品 4 的规格是
    // `60L 600×400×400mm`，尺寸列又是 `600×400×400`，两列说的是同一件事。
    // 现在「规格」显示原始规格文字，尺寸/重量作为次要信息跟在后面。
    {
      // 颜色与材质并进这一列的第二行：它们原来各占一列（56 + 66），
      // 窄到列头都被压成「颜…」「装…」，而内容只有两个字
      title: '规格 / 尺寸',
      width: 175,
      ellipsis: true,
      render: (_: unknown, record: Sku) => {
        const size =
          record.length || record.width || record.height
            ? `${record.length ?? '-'}×${record.width ?? '-'}×${record.height ?? '-'}`
            : null
        const weight = record.weight ? `${record.weight}kg` : null
        const spec = record.specification || ''
        /* 原样是「规格」+「尺寸 · 重量 · 颜色 · 材质」两行，可规格本身往往就写着
           尺寸和颜色（「600×400×320 蓝色」），第二行原封不动再说一遍，还多个尾点，
           列宽 175 又放不下，截成「3.8kg · 蓝色 · 」（2026-11-20 截图）。
           有规格时第二行只留规格没覆盖的补充项（重量、材质）；没规格才把尺寸颜色补上。 */
        const extra = spec
          ? [weight, record.material].filter(Boolean).join(' · ')
          : [size, weight, record.color, record.material].filter(Boolean).join(' · ')
        if (!spec && !extra) return '-'
        return (
          <div>
            <div>{spec || '-'}</div>
            {extra && (
              <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>{extra}</div>
            )}
          </div>
        )
      },
    },
    { title: '起订量', dataIndex: 'moq', width: 64, render: (v: number | null) => v ?? '-' },
    {
      // 原来「装箱数」单独一列（70px）。并进「包装」省一整列宽度，信息不丢：
      // 两者本来就是一起看的（什么包装、一箱装几个）。装箱数为空时只显示包装方式。
      title: '包装 / 箱装',
      width: 110,
      ellipsis: true,
      render: (_: unknown, record: Sku) => {
        const pack = record.package_type || ''
        const per = record.carton_qty ? `${record.carton_qty}/箱` : ''
        const text = [pack, per].filter(Boolean).join(' · ')
        return text || '-'
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 84,
      render: (v: string) => (
        <StatusDot label={v === 'active' ? '在售' : '停用'} tone={v === 'active' ? 'green' : 'grey'} />
      ),
    },
    {
      // 列头原来留空，可这一格夹在「状态」和「成本状态」中间，
      // 下面孤零零一个「来源 / 差异」，读起来像状态的一部分（2026-11-20 截图）。
      // 补一个「主数据」把它认回来。
      title: '主数据',
      // 138 的时候这一格还被表格挤到 ~80 宽，「来源/待核实/差异」折成三行，
      // 每行的行高被它一个人撑到 96（2026-11-20 巡检）。文案收成「来源 / 差异」，
      // 再 nowrap 锁一行 —— 点开面板里「来源、待核实、差异」三块一样都在。
      width: 104,
      render: (_: unknown, record: Sku) => (
        <a
          style={{ color: 'var(--crm-primary)', whiteSpace: 'nowrap' }}
          onClick={() => {
            setResolutions({})
            setNotes({})
            confirmDiffMutation.reset()
            setMasterSku(record)
            // 每次打开都重置为默认勾选（对客三字段）：
            // 上一次的勾选残留会造成"以为只确认重量、结果把名称也确认了"这类误操作。
            setConfirmFields([...QUOTE_FIELD_NAMES])
          }}
        >
          来源 / 差异
        </a>
      ),
    },
    {
      title: (
        <span className="crm-help-label">
          成本状态
          <HelpTip forLabel="成本状态" text="完整表示采购、生产、包装等成本项都录了；缺项会影响核价和报价。" />
        </span>
      ),
      width: 168,
      render: (_: unknown, record: Sku) => (
        // 状态和入口并成一行「● 成本未维护 · 维护成本」：原来一个格子叠两行，
        // 每个 SKU 行都被撑到 100px，「图片」格里的缩略图也跟着悬空（2026-11-21 巡检）。
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
          <StatusDot
            label={record.cost_status?.label ?? '成本状态待查询'}
            tone={record.cost_status?.state === 'complete' ? 'green' : 'orange'}
          />
          {can('price:manage') ? (
            <Link to={`/prices?tab=costs&sku_id=${record.id}`} style={{ fontSize: 12 }}>
              {record.cost_status?.state === 'complete' ? '成本明细' : '去维护'}
            </Link>
          ) : record.cost_status?.state !== 'complete' ? (
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>请联系价格管理员补齐</span>
          ) : null}
        </div>
      ),
    },
    {
      title: '操作',
      // 130 → 185 是因为多了「图片」这一项；现在图片挂在缩略图格上，
      // 操作列收到 145（编辑 / 删除）—— 这是让 1440 窗口不出现横滚的最后一刀。
      // 现在颜色/材质并进规格列后总宽仍超过容器（会横向滚动），
      // 固定在右侧，「图片 / 编辑 / 停用」才不会被滚出视野。
      width: 145,
      fixed: 'right' as const,
      render: (_: unknown, record: Sku) =>
        canManage ? (
          <>
            {/* 看图不要求写权限：后端对读取只要 product:view，
                所以这里对能进产品详情页的人一律显示（写权限由弹窗内的上传/删除按钮自己判）。 */}
            <a
              style={{ color: 'var(--crm-primary)', marginRight: 10 }}
              onClick={() => setImageSku(record)}
            >
              图片
            </a>
            <a
              style={{ color: 'var(--crm-primary)', marginRight: 10 }}
              onClick={() => {
                setEditingSku(record)
                setSkuForm({
                  sku_code: record.sku_code,
                  name: record.name ?? '',
                  specification: record.specification ?? '',
                  color: record.color ?? '',
                  material: record.material ?? '',
                  length: String(record.length ?? ''),
                  width: String(record.width ?? ''),
                  height: String(record.height ?? ''),
                  weight: String(record.weight ?? ''),
                  carton_qty: String(record.carton_qty ?? ''),
                  carton_volume: String(record.carton_volume ?? ''),
                  moq: String(record.moq ?? ''),
                  package_type: record.package_type ?? '',
                  unit: record.unit ?? '件',
                })
                setSkuVisible(true)
              }}
            >
              编辑
            </a>
            <Popconfirm title={`确认${record.status === 'active' ? '停用' : '启用'} SKU「${record.sku_code}」？`} onConfirm={() => toggleMutation.mutate({ id: record.id, enable: record.status !== 'active' })}>
              <a style={{ color: 'var(--crm-primary)' }}>{record.status === 'active' ? '停用' : '启用'}</a>
            </Popconfirm>
          </>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      {notePrompt.node}
      <DetailHeader
        title={product.name}
        tags={
          <>
            {/* 产品线和分类同名时会出现两枚一模一样的标签（截图里两个「缠绕膜」），
                同值只留一个。 */}
            {product.product_line && <Tag color="blue">{product.product_line}</Tag>}
            {product.category && product.category !== product.product_line && <Tag>{product.category}</Tag>}
          </>
        }
        meta={
          <>
            <span>品牌：{product.brand ?? '-'}</span>
            <span>SKU 数：{product.sku_count}</span>
            <span>创建时间：{formatBeijingTime(product.created_at)}</span>
          </>
        }
        extra={
          canManage && (
            <>
              {/* 2026-11-20：这两个按钮原来都是 Semi 默认的浅底药丸（编辑资料=浅蓝、
                  删除产品=浅红）。Semi 不写 theme 时是 light，不是 solid —— 全站那条
                  "主按钮改白底描边"的 CSS 只认 solid，所以漏了这里。现在显式给：
                  编辑资料=白底描边主按钮，删除产品=红字文字按钮。 */}
              <Button
                type="primary"
                theme="outline"
                onClick={() => {
                  setProductForm({
                    name: product.name,
                    product_line: product.product_line ?? '',
                    category: product.category ?? '',
                    brand: product.brand ?? '',
                    description: product.description ?? '',
                  })
                  setEditVisible(true)
                }}
              >
                编辑资料
              </Button>
              <Popconfirm title="删除后该产品及其 SKU 将不可见，确认？" onConfirm={() => deleteMutation.mutate()}>
                <Button type="danger" theme="borderless">删除产品</Button>
              </Popconfirm>
            </>
          )
        }
      >
        {product.description && (
          <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>{product.description}</div>
        )}
      </DetailHeader>

      <SectionCard
        title="SKU 列表"
        extra={
          canManage && (
            <Button type="primary"
              theme="outline"
              onClick={() => {
                setEditingSku(null)
                setSkuForm(EMPTY_SKU)
                setPendingImages([])
                setSkuVisible(true)
              }}
            >
              新建 SKU
            </Button>
          )
        }
      >
        <Table<Sku>
          className="product-sku-table"
          columns={skuColumns}
          dataSource={skuQuery.data ?? []}
          loading={skuQuery.isLoading}
          rowKey="id"
          pagination={false}
          empty="还没有 SKU，先加一个"
          // 与上面各列 width **之和一致**（1123）。写大了会在 1440 窗口下多出 6px 横向滚动条
          // ——列宽总和才是真实内容宽度，`scroll.x` 必须对齐它。
          // 用数字：字符串 max(100%, Npx) 在容器比 N 小时不会真的撑开，列会被压窄
          scroll={{ x: 1107 }}
        />
      </SectionCard>

      {/* 产品资料附件（方案 §7：可上传图片/规格书，可预览） */}
      <SectionCard title="产品资料与图片">
        {/* 产品附件的写入跟产品自己的写权限走（2026-10-07 口径），
            所以要把产品权限码传进去；不传就默认按文件中心，会与后端不一致。 */}
        <AttachmentPanel
          businessType="product"
          businessId={productId}
          writePermission="product:manage"
        />
      </SectionCard>

      <Modal
        width={520}
        title="编辑产品资料"
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => {
          if (!productForm.name.trim()) {
            Toast.warning('产品名称必填')
            return
          }
          updateMutation.mutate(productForm)
        }}
        confirmLoading={updateMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>产品名称</FormLabel>
            <Input value={productForm.name} onChange={(v) => setProductForm({ ...productForm, name: v })} />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>产品线</div>
              <Input
                value={productForm.product_line ?? ''}
                onChange={(v) => setProductForm({ ...productForm, product_line: v })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>分类</div>
              <Input
                value={productForm.category ?? ''}
                onChange={(v) => setProductForm({ ...productForm, category: v })}
                placeholder="自行填写产品分类"
                maxLength={64}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>品牌</div>
            <Input value={productForm.brand ?? ''} onChange={(v) => setProductForm({ ...productForm, brand: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>销售说明</div>
            {/* 销售说明是给客户看的整句话，原来挤在单行输入框里 ——
                写两句就看不见开头。换成两行文本域。 */}
            <TextArea
              rows={2}
              value={productForm.description ?? ''}
              onChange={(v) => setProductForm({ ...productForm, description: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={editingSku ? `编辑 SKU：${editingSku.sku_code}` : '新建 SKU'}
        visible={skuVisible}
        width={680}
        onCancel={() => {
          setSkuVisible(false)
          setEditingSku(null)
        }}
        onOk={() => {
          if (editingSku && !skuForm.sku_code.trim()) {
            Toast.warning('已有编码不能清空')
            return
          }
          skuMutation.mutate({
            sku_code: skuForm.sku_code.trim() || undefined,
            name: skuForm.name.trim() || null,
            specification: skuForm.specification || null,
            color: skuForm.color || null,
            material: skuForm.material || null,
            length: toExactNumeric(skuForm.length),
            width: toExactNumeric(skuForm.width),
            height: toExactNumeric(skuForm.height),
            weight: toExactNumeric(skuForm.weight),
            carton_qty: toExactNumeric(skuForm.carton_qty),
            carton_volume: toExactNumeric(skuForm.carton_volume),
            moq: toExactNumeric(skuForm.moq),
            package_type: skuForm.package_type || null,
            unit: skuForm.unit || null,
          })
        }}
        confirmLoading={skuMutation.isPending}
        /* 编辑态写「保存修改」、新建态才写「创建」：同一个弹窗复用文案会让编辑的人
           以为自己会另建一条（2026-11-17 视觉审视 V2-08） */
        okText={editingSku ? '保存修改' : '创建'}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              {/* 编辑态编码是已有值，不该再挂「新建可不填」这句新建规则 */}
              <FormLabel required={Boolean(editingSku)}>
                {editingSku ? '编码（修改会影响历史单据引用）' : '编码（新建可不填）'}
              </FormLabel>
              <Input
                value={skuForm.sku_code}
                onChange={(v) => setSkuForm({ ...skuForm, sku_code: v })}
                placeholder={editingSku ? '保留公司已有编码' : '有公司编码可填写；留空自动生成'}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>规格</div>
              <Input
                value={skuForm.specification}
                onChange={(v) => setSkuForm({ ...skuForm, specification: v })}
                placeholder="600×400×300mm"
              />
            </div>
          </div>
          <div>
            {/* SKU 名称：**选填**（主人 2026-10-10）。后端与 CSV 一直支持它
                （模型有 `name`、`SkuCreate`/`SkuUpdate` 都有），但界面从前没有输入框
                —— 于是种子数据里 `LL-100L-WH` 的名字被错写成"60L"也没人发现。
                它和「规格」不是一回事：规格是参数（60L 600×400×400mm），
                名称是给人看的一句话（冷链保温箱 60L 白色）。 */}
            <div style={{ marginBottom: 4 }}>规格名称（选填，未填时显示产品名称）</div>
            <Input
              value={skuForm.name}
              onChange={(v) => setSkuForm({ ...skuForm, name: v })}
              placeholder="例如：冷链保温箱 60L 白色"
              maxLength={200}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>颜色</div>
              <Input value={skuForm.color} onChange={(v) => setSkuForm({ ...skuForm, color: v })} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>材质</div>
              <Input
                value={skuForm.material}
                onChange={(v) => setSkuForm({ ...skuForm, material: v })}
              />
            </div>
          </div>

          {/* 图片（可选）：**只在新建时出现** —— 编辑态 SKU 已有 id，
              点列表里的图片格直接传更直接，不必在这绕一层暂存。
              这里只把 File 留在内存里，点保存时先建 SKU、拿到 id 再逐张上传。 */}
          {!editingSku && (
            <div>
              <div style={{ marginBottom: 4 }}>图片（可选，保存时一并上传）</div>
              <PendingImagePicker
                files={pendingImages}
                onChange={setPendingImages}
                disabled={skuMutation.isPending}
              />
            </div>
          )}
          {/* 尺寸/重量这些数字字段原来排成「3 列 + 4 列」两行 —— 左边缘对不齐，
              一行只有三格时右边还空一格，看着像漏填了什么。
              统一成四列两行，纵向一条线；单位从包装方式那行提上来，
              包装方式单独通栏（它的例子是三个词，短了放不下）。 */}
          <div style={{ display: 'flex', gap: 12 }}>
            {(
              [
                ['length', '长 (mm)'],
                ['width', '宽 (mm)'],
                ['height', '高 (mm)'],
                ['weight', '单重 (kg)'],
              ] as const
            ).map(([field, label]) => (
              <div style={{ flex: 1 }} key={field}>
                <div style={{ marginBottom: 4 }}>{label}</div>
                <Input
                  value={skuForm[field]}
                  onChange={(v) => setSkuForm({ ...skuForm, [field]: v })}
                  placeholder="数字"
                />
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            {(
              [
                ['carton_qty', '装箱数'],
                ['carton_volume', '箱体积 (m³)'],
                ['moq', '起订量（MOQ）'],
                ['unit', '单位'],
              ] as const
            ).map(([field, label]) => (
              <div style={{ flex: 1 }} key={field}>
                <div style={{ marginBottom: 4 }}>{label}</div>
                <Input
                  value={skuForm[field]}
                  onChange={(v) => setSkuForm({ ...skuForm, [field]: v })}
                  placeholder={field === 'unit' ? '件' : '数字'}
                />
              </div>
            ))}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>包装方式</div>
            <Input
              value={skuForm.package_type}
              onChange={(v) => setSkuForm({ ...skuForm, package_type: v })}
              placeholder="编织袋 / 托盘 / 纸箱"
            />
          </div>
        </div>
      </Modal>

      {/*
        第八批 §8.14：每个关键字段的**来源、更新时间、外部身份与人工确认版本**，
        以及待确认差异的人工核定入口。
        来源未核实一律显示「待核实」、权威归属未定显示「未拍板」——
        这两句话来自后端数据（`source_status` / `authority_label`），不是页面写死的文案。
      */}
      <Modal
        title={masterSku ? `主数据来源与差异：${masterSku.sku_code}` : '主数据来源与差异'}
        visible={masterSku !== null}
        width={MASTER_MODAL_WIDTH}
        // 窄屏兜底：分屏或小窗口下不让弹窗顶出屏幕，宽度不足时表格内部横向滚动。
        style={{ maxWidth: 'calc(100vw - 48px)' }}
        footer={null}
        onCancel={() => setMasterSku(null)}
      >
        {masterQuery.isLoading && <div>加载中…</div>}
        {masterQuery.isError && emptyTextNode(masterQuery, '暂无主数据资料')}
        {masterQuery.data && !masterQuery.isError && (
          <div className="sku-master-overview">
            <div className={`sku-master-status${!quoteFieldsConfirmed || changedSinceConfirm.length ? ' sku-master-status-attention' : ''}`}>
              <div>
                <strong>{quoteFieldsConfirmed ? '报价资料已确认' : '报价资料待确认'}</strong>
                <p className="workflow-guide">
                  {quoteFieldsConfirmed
                    ? '名称、规格和单位已有确认版本。正式发送前，报价明细也要引用对应的确认资料。'
                    : `${quoteFieldsMissingLabels.join('、') || '部分报价字段'}尚未确认。先补齐报价资料，再到报价页核对明细使用的版本。`}
                </p>
                {changedSinceConfirm.length > 0 && (
                  <p className="workflow-guide">这些本地字段已修改，等重新核对确认：{changedSinceConfirm.join('、')}。</p>
                )}
                {!can('product:manage') && (!quoteFieldsConfirmed || changedSinceConfirm.length > 0) && (
                  <p className="workflow-guide">请联系有商品维护权限的同事核对并确认。</p>
                )}
              </div>
              {can('product:manage') && (
                <>
                  <div>
                    <p className="workflow-guide">核对本地名称、规格和单位，有变化时会保存新的确认版本。</p>
                    <CheckboxGroup className="sku-master-checkboxes"
                      value={confirmFields.filter((field) => QUOTE_FIELD_NAMES.includes(field))}
                      onChange={(values) => setConfirmFields((current) => [
                        ...current.filter((field) => !QUOTE_FIELD_NAMES.includes(field)), ...values as string[],
                      ])}>
                      {masterQuery.data.fields.filter((field) => QUOTE_FIELD_NAMES.includes(field.field_name)).map((field) => (
                        <Checkbox key={field.field_name} value={field.field_name}>{field.field_label}</Checkbox>
                      ))}
                    </CheckboxGroup>
                    <details className="sku-master-optional">
                      <summary>其他字段（按需确认）</summary>
                      <CheckboxGroup className="sku-master-checkboxes"
                        value={confirmFields.filter((field) => !QUOTE_FIELD_NAMES.includes(field))}
                        onChange={(values) => setConfirmFields((current) => [
                          ...current.filter((field) => QUOTE_FIELD_NAMES.includes(field)), ...values as string[],
                        ])}>
                        {masterQuery.data.fields.filter((field) => !QUOTE_FIELD_NAMES.includes(field.field_name)).map((field) => (
                          <Checkbox key={field.field_name} value={field.field_name}>
                            {field.field_label}
                            {FREIGHT_FIELD_NAMES.includes(field.field_name) && (
                              <Tag size="small" color="blue" style={{ marginLeft: 4 }}>参与运费计算</Tag>
                            )}
                          </Checkbox>
                        ))}
                      </CheckboxGroup>
                    </details>
                  </div>

                  <div>
                    <Popconfirm
                      title="确认本地主数据？"
                      content={`将按当前本地值确认这 ${confirmFields.length} 个字段。`}
                      onConfirm={() => {
                        if (!masterSku || !confirmFields.length) return
                        confirmLocalMutation.mutate({
                          skuId: masterSku.id,
                          fields: confirmFields,
                        })
                      }}
                    >
                      <Button theme="outline" type="warning" disabled={!confirmFields.length} loading={confirmLocalMutation.isPending}>
                        确认选中的 {confirmFields.length} 个字段
                      </Button>
                    </Popconfirm>
                  </div>
                </>
              )}
            </div>
            <section className="sku-master-differences" aria-label="待确认差异">
              <div className="sku-master-diff-heading">
                <strong>待确认差异（{masterQuery.data.pending_diff_count}）</strong>
                <span>
                  {masterQuery.data.latest_confirmed_version > 0
                    ? `最近已确认版本 v${masterQuery.data.latest_confirmed_version}`
                    : '暂无人工确认版本'}
                </span>
              </div>
              {masterQuery.data.pending_diffs.length === 0 && (
                <div className="sku-master-diff-detail">没有待确认差异</div>
              )}
              {masterQuery.data.pending_diffs.map((diff: SkuMasterDiff) => {
                const resolution = resolutions[diff.id] ?? ''
                const note = notes[diff.id] ?? ''
                const blockedReason = !diff.allowed_resolutions.includes(resolution)
                  ? '先选择如何处理这条差异'
                  : diff.requires_note && !note.trim()
                    ? '请填写核定依据，例如核对了哪份资料'
                    : ''
                const effect = diff.field_name && resolution === 'keep_local'
                  ? '保留当前本地值，并将它记为确认值。'
                  : diff.field_name && resolution === 'take_external'
                    ? '来源符合该字段的维护规则时，会用外部值更新本地资料并记录确认版本。'
                    : ''
                return (
                  <div key={diff.id} className="sku-master-diff">
                    <div className="sku-master-diff-heading">
                      <strong>{diff.field_label || 'SKU 资料'}</strong>
                      <Tag color="orange">{DIFF_TYPE_LABELS[diff.diff_type] ?? diff.diff_type}</Tag>
                    </div>
                    <dl className="sku-master-diff-values">
                      <div>
                        <dt>本地值</dt>
                        <dd>{String(diff.current_value ?? '未填写')}</dd>
                      </div>
                      <div>
                        <dt>外部来源值{diff.system_type ? `（${diff.system_type}）` : ''}</dt>
                        <dd>{String(diff.incoming_value ?? '空值')}</dd>
                      </div>
                    </dl>
                    {canManage ? (
                      <>
                        <div className="sku-master-diff-fields">
                          <div>
                            <FormLabel required>处理方式</FormLabel>
                            <Select
                              aria-label="处理方式"
                              style={{ width: '100%' }}
                              placeholder="选择处理方式"
                              value={resolution}
                              onChange={(value: unknown) =>
                                setResolutions((previous) => ({ ...previous, [diff.id]: String(value) }))
                              }
                              optionList={diff.allowed_resolutions.map((item) => ({
                                value: item,
                                label: RESOLUTION_LABELS[item] ?? item,
                              }))}
                            />
                          </div>
                          <div>
                            <FormLabel required={diff.requires_note}>{diff.requires_note ? '核定依据' : '备注（可选）'}</FormLabel>
                            <Input
                              aria-label={diff.requires_note ? '核定依据' : '备注'}
                              placeholder={diff.requires_note ? '必填，例如已核对样品标签' : '例如已与产品负责人确认'}
                              value={note}
                              onChange={(value: string) => setNotes((previous) => ({ ...previous, [diff.id]: value }))}
                            />
                          </div>
                        </div>
                        {effect && <p className="sku-master-diff-detail">{effect}</p>}
                        <GuardedButton
                          action="确认结论"
                          theme="outline"
                          disabled={Boolean(blockedReason)}
                          blockedReasons={blockedReason ? [blockedReason] : []}
                          loading={confirmDiffMutation.isPending}
                          onClick={() => confirmDiffMutation.mutate({ diffId: diff.id, resolution, note })}
                        >
                          确认结论
                        </GuardedButton>
                      </>
                    ) : (
                      <p className="sku-master-diff-detail">需要产品维护权限才能核定。可以请产品负责人核对这条差异。</p>
                    )}
                    {confirmDiffMutation.error && confirmDiffMutation.variables?.diffId === diff.id && (
                      <Banner type="danger" fullMode={false} closeIcon={null} description={confirmDiffMutation.error.message}>
                        <Button theme="borderless" loading={masterQuery.isFetching} onClick={async () => {
                          const result = await masterQuery.refetch()
                          if (result.isError) {
                            Toast.error(result.error.message)
                            return
                          }
                          setResolutions((previous) => ({ ...previous, [diff.id]: '' }))
                          confirmDiffMutation.reset()
                        }}>刷新差异</Button>
                      </Banner>
                    )}
                  </div>
                )
              })}
            </section>
            {/* 逐条一行：原来用「；」连成一大段，在窄弹窗里挤成好几行、断句也难看 */}
            <ul
              style={{
                margin: 0,
                paddingLeft: 18,
                color: 'var(--crm-text-2)',
                fontSize: 13,
                lineHeight: 1.7,
              }}
            >
              {masterQuery.data.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>外部来源台账</div>
              <Table<SkuIdentitySource>
                size="small"
                pagination={false}
                loading={identityQuery.isLoading}
                dataSource={identityQuery.data?.items ?? []}
                rowKey="id"
                columns={[
                  { title: '来源系统', dataIndex: 'system_type', width: 110 },
                  { title: '外部编码', dataIndex: 'external_code', width: 150 },
                  { title: '外部名称', dataIndex: 'external_name', width: 150, render: (v: string | null) => v ?? '-' },
                  {
                    title: '状态', dataIndex: 'match_status', width: 100,
                    render: (v: string) => ({ matched: '已匹配', pending: '待匹配', conflict: '有冲突', renamed: '已改码', stopped: '已停用' }[v] ?? v),
                  },
                  ...(canManage ? [{
                    title: '操作', width: 220,
                    render: (_: unknown, source: SkuIdentitySource) => (
                      <span style={{ display: 'inline-flex', gap: 8 }}>
                        {source.match_status === 'pending' && (
                          <Button size="small" theme="borderless" onClick={() => identityActionMutation.mutate({ action: 'replay', source })}>重放匹配</Button>
                        )}
                        {source.match_status !== 'stopped' && (
                          <>
                            <Button size="small" theme="borderless" onClick={() => identityActionMutation.mutate({ action: 'rename', source })}>登记改码</Button>
                            <Button size="small" theme="borderless" type="danger" onClick={() => identityActionMutation.mutate({ action: 'stop', source })}>标记停用</Button>
                          </>
                        )}
                      </span>
                    ),
                  }] : []),
                ]}
                scroll={{ x: canManage ? 730 : 510 }}
                empty={emptyTextNode(identityQuery, '暂无外部来源记录')}
              />
            </div>
            <Table<SkuMasterField>
              columns={masterFieldColumns}
              dataSource={masterQuery.data.fields}
              rowKey="field_name"
              size="small"
              pagination={false}
              scroll={{ x: `max(100%, ${MASTER_TABLE_WIDTH}px)` }}
            />
          </div>
        )}
      </Modal>
      {/* SKU 图片弹窗：产品挂主图/系列图，SKU 挂各型号实拍图（2026-10-10 主人拍板 B）。
          SKU 行可能很多，所以这一级用弹窗而不是常驻区块。 */}
      <Modal
        title={imageSku ? `图片：${imageSku.sku_code}` : '图片'}
        visible={imageSku !== null}
        onCancel={() => setImageSku(null)}
        footer={null}
        width={900}
        bodyStyle={{ padding: 16 }}
      >
        {imageSku && (
          <ImageGallery
            businessType="sku"
            businessId={imageSku.id}
            writePermission="product:manage"
          />
        )}
      </Modal>
    </div>
  )
}
