import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  confirmLocalSkuMaster,
  confirmSkuMasterDiff,
  createSku,
  deleteProduct,
  disableSku,
  enableSku,
  getProduct,
  getSkuMaster,
  listProductSkus,
  updateProduct,
  updateSku,
  type ProductPayload,
  type SkuMasterDiff,
  type SkuMasterField,
  type SkuPayload,
} from '../../shared/api/product'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import AttachmentPanel from '../common/AttachmentPanel'
import ImageGallery from '../../shared/components/ImageGallery'
import SkuImageCell from './SkuImageCell'
import PendingImagePicker from './PendingImagePicker'
import { listBusinessFilesBatch, uploadFile, type FileRow } from '../../shared/api/file'
import SectionCard from '../../shared/components/SectionCard'
import type { Sku } from '../../shared/types'
import FormLabel from '../../shared/components/FormLabel'

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

const toNumber = (value: string): number | null => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

/** 正式报价对客要印的三个字段，与后端 `master.QUOTE_DISPLAY_FIELDS` 对齐。 */
const QUOTE_FIELD_NAMES: readonly string[] = ['name', 'specification', 'unit']
const QUOTE_FIELD_LABELS = ['名称', '规格', '单位']

export default function ProductDetailPage() {
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
  const [resolutions, setResolutions] = useState<Record<number, string>>({})
  const [notes, setNotes] = useState<Record<number, string>>({})

  const productQuery = useQuery({
    queryKey: ['product', productId],
    queryFn: () => getProduct(productId),
    enabled: Number.isFinite(productId),
  })
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

  const confirmDiffMutation = useMutation({
    mutationFn: ({ diffId, resolution, note }: { diffId: number; resolution: string; note?: string }) =>
      confirmSkuMasterDiff(diffId, { resolution, note }),
    onSuccess: (data) => {
      Toast.success(data.message)
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
      void queryClient.invalidateQueries({ queryKey: ['product-skus', productId] })
    },
    onError: (error: Error) => Toast.error(error.message),
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

  const confirmLocalMutation = useMutation({
    mutationFn: (skuId: number) => confirmLocalSkuMaster(skuId, { note: '产品详情页本地核对确认' }),
    onSuccess: (data) => {
      Toast.success(data.message)
      void queryClient.invalidateQueries({ queryKey: ['sku-master', masterSku?.id] })
      void queryClient.invalidateQueries({ queryKey: ['product-skus', productId] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const product = productQuery.data
  if (productQuery.isLoading) return <div className="page-container">加载中…</div>
  if (!product) return <div className="page-container">产品不存在</div>

  /**
   * 字段级"来源 / 来源状态 / 更新时间 / 权威归属 / 确认版本"。
   *
   * 列宽合计必须 ≤ 弹窗内容区宽度（MASTER_MODAL_WIDTH 减去左右内边距）。
   * 原来合计 910px，而 920 宽的弹窗扣掉内边距只剩约 872px —— 表格放不下，
   * 最后一列「状态」被挤到可视区之外，表头也被压成两行。
   * 现在按"表头文字宽 + 内边距"逐列量过，合计 870px，弹窗加宽后留有余量。
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
      render: (_: unknown, record: SkuMasterField) => record.source_system ?? '—',
    },
    {
      title: '来源状态',
      width: 92,
      render: (_: unknown, record: SkuMasterField) =>
        record.source_verified ? <Tag color="green">已核实</Tag> : <Tag>待核实</Tag>,
    },
    {
      title: '来源更新时间',
      width: 150,
      render: (_: unknown, record: SkuMasterField) =>
        record.source_updated_at ? new Date(record.source_updated_at).toLocaleString('zh-CN') : '—',
    },
    {
      title: '权威归属',
      width: 100,
      render: (_: unknown, record: SkuMasterField) => record.authority_label,
    },
    {
      title: '确认版本',
      width: 92,
      render: (_: unknown, record: SkuMasterField) =>
        record.confirmed_version > 0 ? `v${record.confirmed_version}` : '—',
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
  const MASTER_TABLE_WIDTH = 870
  /** 弹窗外宽；窄屏（笔记本、分屏）由 maxWidth 兜住，不会顶出屏幕。 */
  const MASTER_MODAL_WIDTH = 1080

  const skuColumns = [
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 96, ellipsis: true },
    {
      // 单开一列（主人 2026-10-10："单开一列，不要横向滚动"）。
      // 宽度是**算出来的**：原来 13 列合计 1595px，1600 窗口下可用只有 1294px，
      // 即"正常窗口下本来就在横滚"。所以这一列的位置靠合并冗余列 + 压窄换出来
      // （详见下面「规格·尺寸·重量」那一列的注释）。
      title: 'SKU 名称',
      dataIndex: 'name',
      width: 150,
      ellipsis: true,
      render: (v: string | null) => v || '-',
    },
    {
      // 图片就放在 SKU 列表这儿（主人 2026-10-10 口径）：直接看到图，
      // 点一下开图片墙（看全部 / 上传 / 删除）。不再单独开"产品图片"区块。
      title: '图片',
      width: 66,
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
      title: '规格 / 尺寸',
      width: 178,
      ellipsis: true,
      render: (_: unknown, record: Sku) => {
        const size =
          record.length || record.width || record.height
            ? `${record.length ?? '-'}×${record.width ?? '-'}×${record.height ?? '-'}`
            : null
        const weight = record.weight ? `${record.weight}kg` : null
        const extra = [size, weight].filter(Boolean).join(' · ')
        const spec = record.specification || ''
        if (!spec && !extra) return '-'
        return (
          <span>
            {spec || '-'}
            {extra && (
              <span style={{ color: 'var(--crm-text-3)', marginLeft: 6 }}>({extra})</span>
            )}
          </span>
        )
      },
    },
    { title: '颜色', dataIndex: 'color', width: 56, ellipsis: true, render: (v: string | null) => v ?? '-' },
    { title: '材质', dataIndex: 'material', width: 66, ellipsis: true, render: (v: string | null) => v ?? '-' },
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
      width: 64,
      render: (v: string) => (v === 'active' ? <Tag color="green">在售</Tag> : <Tag>停用</Tag>),
    },
    {
      // 列头留空：内容本身就是"来源/待核实/差异"这个入口，
      // 标题再写一遍"来源"是重复，还白占宽度。
      title: '',
      width: 120,
      render: (_: unknown, record: Sku) => (
        <a
          style={{ color: 'var(--crm-primary)' }}
          onClick={() => {
            setResolutions({})
            setNotes({})
            setMasterSku(record)
          }}
        >
          来源/待核实/差异
        </a>
      ),
    },
    {
      title: '操作',
      // 130 → 185 是因为多了「图片」这一项；现在图片挂在缩略图格上，
      // 操作列收到 145（编辑 / 删除）—— 这是让 1440 窗口不出现横滚的最后一刀。
      width: 145,
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
            <a
              style={{ color: 'var(--crm-primary)' }}
              onClick={() => toggleMutation.mutate({ id: record.id, enable: record.status !== 'active' })}
            >
              {record.status === 'active' ? '停用' : '启用'}
            </a>
          </>
        ) : (
          '-'
        ),
    },
  ]

  return (
    <div className="page-container">
      <DetailHeader
        title={product.name}
        tags={
          <>
            {product.product_line && <Tag color="blue">{product.product_line}</Tag>}
            {product.category && <Tag>{product.category}</Tag>}
          </>
        }
        meta={
          <>
            <span>品牌：{product.brand ?? '-'}</span>
            <span>SKU 数：{product.sku_count}</span>
            <span>创建时间：{new Date(product.created_at).toLocaleString('zh-CN')}</span>
          </>
        }
        extra={
          canManage && (
            <>
              <Button
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
                <Button type="danger">删除产品</Button>
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
            <Button
              theme="solid"
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
          columns={skuColumns}
          dataSource={skuQuery.data ?? []}
          loading={skuQuery.isLoading}
          rowKey="id"
          pagination={false}
          empty="还没有 SKU，先加一个"
          // 与上面各列 width **之和一致**（1123）。写大了会在 1440 窗口下多出 6px 横向滚动条
          // ——列宽总和才是真实内容宽度，`scroll.x` 必须对齐它。
          scroll={{ x: 1115 }}
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
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>品牌</div>
            <Input value={productForm.brand ?? ''} onChange={(v) => setProductForm({ ...productForm, brand: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>销售说明</div>
            <Input
              value={productForm.description ?? ''}
              onChange={(v) => setProductForm({ ...productForm, description: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={editingSku ? `编辑 SKU：${editingSku.sku_code}` : '新建 SKU'}
        visible={skuVisible}
        width={640}
        onCancel={() => {
          setSkuVisible(false)
          setEditingSku(null)
        }}
        onOk={() => {
          if (!skuForm.sku_code.trim()) {
            Toast.warning('SKU 编码必填')
            return
          }
          skuMutation.mutate({
            sku_code: skuForm.sku_code.trim(),
            specification: skuForm.specification || null,
            color: skuForm.color || null,
            material: skuForm.material || null,
            length: toNumber(skuForm.length),
            width: toNumber(skuForm.width),
            height: toNumber(skuForm.height),
            weight: toNumber(skuForm.weight),
            carton_qty: toNumber(skuForm.carton_qty),
            carton_volume: toNumber(skuForm.carton_volume),
            moq: toNumber(skuForm.moq),
            package_type: skuForm.package_type || null,
            unit: skuForm.unit || null,
          })
        }}
        confirmLoading={skuMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <FormLabel required>SKU 编码</FormLabel>
              <Input
                value={skuForm.sku_code}
                onChange={(v) => setSkuForm({ ...skuForm, sku_code: v })}
                placeholder="ZX-6040-B"
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
            <div style={{ marginBottom: 4 }}>SKU 名称（选填）</div>
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
          <div style={{ display: 'flex', gap: 12 }}>
            {(
              [
                ['length', '长 (mm)'],
                ['width', '宽 (mm)'],
                ['height', '高 (mm)'],
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
                ['weight', '单重 (kg)'],
                ['carton_qty', '装箱数'],
                ['carton_volume', '箱体积 (m³)'],
                ['moq', '起订量（MOQ）'],
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
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>包装方式</div>
              <Input
                value={skuForm.package_type}
                onChange={(v) => setSkuForm({ ...skuForm, package_type: v })}
                placeholder="编织袋 / 托盘 / 纸箱"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>单位</div>
              <Input value={skuForm.unit} onChange={(v) => setSkuForm({ ...skuForm, unit: v })} />
            </div>
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
        {masterQuery.data && (
          <div style={{ display: 'grid', gap: 16 }}>
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
            <Table<SkuMasterField>
              columns={masterFieldColumns}
              dataSource={masterQuery.data.fields}
              rowKey="field_name"
              size="small"
              pagination={false}
              scroll={{ x: MASTER_TABLE_WIDTH }}
            />
            {/*
              本地直接确认入口（审查 2026-10-10 实测的 P1 流程阻断）：
              本地自建的 SKU 既没有确认记录、也没有差异记录，唯一的确认动作又挂在
              差异上 —— 正式报价因此永久被拦、页面上却**没有任何按钮**可点。
              「从外部导入一次」也不是可靠出口：来源值与本地一致时零差异，仍然没入口。
              所以这里给一个不依赖差异的确认按钮：有权限的人核对后确认本地值。
            */}
            {can('product:manage') && !quoteFieldsConfirmed && (
              <div
                style={{
                  border: '1px solid var(--semi-color-warning)',
                  background: 'var(--semi-color-warning-light-default)',
                  borderRadius: 6,
                  padding: 12,
                  display: 'grid',
                  gap: 8,
                }}
              >
                <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                  正式报价只能引用**已确认**的主数据版本。这个 SKU 的
                  {quoteFieldsMissingLabels.length > 0
                    ? `「${quoteFieldsMissingLabels.join('、')}」还没有人工确认过`
                    : '对客字段还没有人工确认过'}
                  ，因此**无法正式发送**。
                  核对无误后点下面的按钮确认（会记下操作人与时间，并冻结一版快照）。
                </div>
                <div>
                  <Popconfirm
                    title="确认本地主数据？"
                    content={`将按当前本地值确认「${QUOTE_FIELD_LABELS.join('、')}」并生成一版快照。`}
                    onConfirm={() => confirmLocalMutation.mutate(masterSku!.id)}
                  >
                    <Button
                      theme="solid"
                      type="warning"
                      loading={confirmLocalMutation.isPending}
                    >
                      确认本地主数据（名称/规格/单位）
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            )}
            <div>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>
                待确认差异（{masterQuery.data.pending_diff_count}）
                {masterQuery.data.latest_confirmed_version > 0
                  ? `｜最近已确认版本 v${masterQuery.data.latest_confirmed_version}`
                  : '｜还没有人工确认过的主数据版本'}
              </div>
              {masterQuery.data.pending_diffs.length === 0 && (
                <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>没有待确认差异</div>
              )}
              {masterQuery.data.pending_diffs.map((diff: SkuMasterDiff) => (
                <div
                  key={diff.id}
                  style={{
                    border: '1px solid var(--semi-color-border)',
                    borderRadius: 6,
                    padding: 10,
                    marginBottom: 8,
                    display: 'grid',
                    gap: 8,
                  }}
                >
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Tag color="orange">{DIFF_TYPE_LABELS[diff.diff_type] ?? diff.diff_type}</Tag>
                    {diff.field_label && <span>{diff.field_label}</span>}
                    <span style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
                      本地 {String(diff.current_value ?? '—')} → 来源{' '}
                      {diff.incoming_value === null || diff.incoming_value === undefined
                        ? '空值'
                        : String(diff.incoming_value)}
                    </span>
                    {diff.requires_note && <Tag>核定需写依据</Tag>}
                  </div>
                  {canManage ? (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Select
                        style={{ width: 220 }}
                        placeholder="选择核定结论"
                        value={resolutions[diff.id]}
                        onChange={(value: unknown) =>
                          setResolutions({ ...resolutions, [diff.id]: String(value) })
                        }
                        optionList={diff.allowed_resolutions.map((item) => ({
                          value: item,
                          label: RESOLUTION_LABELS[item] ?? item,
                        }))}
                      />
                      <Input
                        style={{ flex: 1 }}
                        placeholder={diff.requires_note ? '必填：依据 / 来源与时点' : '备注（可选）'}
                        value={notes[diff.id] ?? ''}
                        onChange={(value: string) => setNotes({ ...notes, [diff.id]: value })}
                      />
                      <Button
                        theme="solid"
                        loading={confirmDiffMutation.isPending}
                        onClick={() => {
                          const resolution = resolutions[diff.id]
                          if (!resolution) {
                            Toast.warning('先选择核定结论')
                            return
                          }
                          if (diff.requires_note && !(notes[diff.id] ?? '').trim()) {
                            Toast.warning('这条差异必须写清依据')
                            return
                          }
                          confirmDiffMutation.mutate({
                            diffId: diff.id,
                            resolution,
                            note: notes[diff.id],
                          })
                        }}
                      >
                        核定
                      </Button>
                    </div>
                  ) : (
                    <div style={{ color: 'var(--crm-text-2)', fontSize: 13 }}>
                      需要产品维护权限才能核定
                    </div>
                  )}
                </div>
              ))}
            </div>
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
        width={880}
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
