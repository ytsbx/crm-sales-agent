import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  createSku,
  deleteProduct,
  disableSku,
  enableSku,
  getProduct,
  listProductSkus,
  updateProduct,
  updateSku,
  type ProductPayload,
  type SkuPayload,
} from '../../shared/api/product'
import { usePermissions } from '../../shared/hooks/permissions'
import DetailHeader from '../../shared/components/DetailHeader'
import AttachmentPanel from '../common/AttachmentPanel'
import SectionCard from '../../shared/components/SectionCard'
import type { Sku } from '../../shared/types'
import FormLabel from '../../shared/components/FormLabel'

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

  const product = productQuery.data
  if (productQuery.isLoading) return <div className="page-container">加载中…</div>
  if (!product) return <div className="page-container">产品不存在</div>

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

      {/* 产品资料/图片附件（方案 §7：产品图片接通；可上传图片/规格书，可预览） */}
      <SectionCard title="产品资料与图片">
        <AttachmentPanel businessType="product" businessId={productId} />
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
    </div>
  )
}
