import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
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
import ProductImageGallery from '../../shared/components/ProductImageGallery'
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
    mutationFn: (payload: SkuPayload) =>
      editingSku ? updateSku(editingSku.id, payload) : createSku(productId, payload),
    onSuccess: () => {
      Toast.success(editingSku ? 'SKU 已保存' : 'SKU 已创建')
      setSkuVisible(false)
      setEditingSku(null)
      setSkuForm(EMPTY_SKU)
      refresh()
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
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 140 },
    { title: '规格', dataIndex: 'specification', width: 200, render: (v: string | null) => v ?? '-' },
    { title: '颜色', dataIndex: 'color', width: 90, render: (v: string | null) => v ?? '-' },
    { title: '材质', dataIndex: 'material', width: 120, render: (v: string | null) => v ?? '-' },
    {
      title: '尺寸 (长×宽×高)',
      width: 180,
      render: (_: unknown, record: Sku) =>
        record.length || record.width || record.height
          ? `${record.length ?? '-'}×${record.width ?? '-'}×${record.height ?? '-'}`
          : '-',
    },
    {
      title: '重量 (kg)',
      dataIndex: 'weight',
      width: 100,
      render: (v: number | null) => v ?? '-',
    },
    { title: '装箱数', dataIndex: 'carton_qty', width: 90, render: (v: number | null) => v ?? '-' },
    { title: 'MOQ', dataIndex: 'moq', width: 90, render: (v: number | null) => v ?? '-' },
    { title: '包装', dataIndex: 'package_type', width: 100, render: (v: string | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? <Tag color="green">在售</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '主数据来源',
      width: 130,
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
      width: 130,
      render: (_: unknown, record: Sku) =>
        canManage ? (
          <>
            <a
              style={{ color: 'var(--crm-primary)', marginRight: 10 }}
              onClick={() => {
                setEditingSku(record)
                setSkuForm({
                  sku_code: record.sku_code,
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
          scroll={{ x: 1200 }}
        />
      </SectionCard>

      {/* 产品图片（方案 §7）：以图为主，缩略图墙 + 点开看大图。
          与下面的「产品资料与图片」分开：这里只收图片、看的是图本身；
          资料那边是通用附件表（图纸、规格书、回款凭证…），看的是文件名。
          混在一张表里既看不清图，也容易把"图片"和"资料"混为一谈。 */}
      <SectionCard title="产品图片">
        <ProductImageGallery
          businessType="product"
          businessId={productId}
          writePermission="product:manage"
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
                ['moq', 'MOQ'],
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
    </div>
  )
}
