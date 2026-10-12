import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { createProduct, listProducts, type ProductPayload } from '../../shared/api/product'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Product } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { useBlockedNotify } from '../../shared/components/BlockedHint'
import { listDictionaries } from '../../shared/api/settings'

const EMPTY_FORM: ProductPayload = {
  name: '',
  product_line: '',
  category: '',
  brand: '',
  description: '',
}

/**
 * 业务员（SALES，没有 `product:manage`）在产品中心看到的那句话。
 *
 * 为什么要有这句：模板下载 / 导入 / 导出整组都挂在 `product:manage` 上，
 * 从前的写法是**整组不渲染** —— 业务员看到工具栏右侧一片空白，界面上一个字都不说，
 * 只会以为"产品模板、导入导出这些功能还没做"（主人 2026-10-12：「点又点不了，
 * 又不给提示为什么点不了，类似的情况都要修」）。
 *
 * 同一权限也把着「新建产品」，所以一并写进来：只说一半，用户还得再猜一次。
 * ⚠️ 权限判据没有动，仍然只有 `can('product:manage')` 才渲染那组控件。
 */
const PRODUCT_MANAGE_PERMISSION_REASON =
  '产品与 SKU 的模板下载、导入、导出需要「产品管理」权限；请联系管理员开通或由产品负责人操作。「新建产品」同样需要这个权限。'

interface ImportResult {
  created_count: number
  updated_count?: number
  skipped_count: number
  failed: Array<{ name: string; reason: string }>
}

export default function ProductListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  // 没有「产品管理」权限时，工具栏上那组模板/导入导出**不渲染**（判据照旧，没动），
  // 但要有一行灰字说清它们为什么不在、找谁能开；点这行灰字会弹一句同样的话
  // （看得见 + 点得动，见 shared/components/BlockedHint.tsx）。
  const canManageProduct = can('product:manage')
  const notifyManageBlocked = useBlockedNotify(
    canManageProduct ? [] : [PRODUCT_MANAGE_PERMISSION_REASON],
  )

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [modalVisible, setModalVisible] = useState(false)
  const [form, setForm] = useState<ProductPayload>(EMPTY_FORM)
  const categoriesQuery = useQuery({
    queryKey: ['dictionaries', 'product_category'],
    queryFn: () => listDictionaries('product_category'),
  })

  // 批量导入/导出（产品报价中心 · 第一批：资料库整理入口）
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [importKind, setImportKind] = useState<'products' | 'skus'>('products')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)

  const tokenHeader = () => {
    const token = JSON.parse(localStorage.getItem('crm-auth') ?? '{}')?.state?.token
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  const downloadCsv = async (url: string, filename: string) => {
    const { default: axios } = await import('axios')
    const response = await axios.get(url, { responseType: 'blob', headers: tokenHeader() })
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  const handleImport = async (file: File) => {
    setImporting(true)
    try {
      const { default: axios } = await import('axios')
      const formData = new FormData()
      formData.append('file', file)
      const response = await axios.post(`/api/v1/${importKind}/import`, formData, {
        headers: tokenHeader(),
      })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message)
      Toast.success(body.message)
      setImportResult(body.data)
      void queryClient.invalidateQueries({ queryKey: ['products'] })
      void queryClient.invalidateQueries({ queryKey: ['skus'] })
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const query = useQuery({
    queryKey: ['products', { keyword, page, pageSize }],
    queryFn: () => listProducts({ keyword, page, page_size: pageSize }),
  })

  const createMutation = useMutation({
    mutationFn: (payload: ProductPayload) => createProduct(payload),
    onSuccess: (product) => {
      Toast.success(`产品「${product.name}」已创建`)
      setModalVisible(false)
      setForm(EMPTY_FORM)
      void queryClient.invalidateQueries({ queryKey: ['products'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    {
      title: '产品名称',
      dataIndex: 'name',
      render: (text: string, record: Product) => (
        <a style={{ color: 'var(--crm-primary)' }} onClick={() => navigate(`/products/${record.id}`)}>
          {text}
        </a>
      ),
    },
    { title: '产品线', dataIndex: 'product_line', width: 140, render: (v: string | null) => v ?? '-' },
    { title: '分类', dataIndex: 'category', width: 120, render: (v: string | null) => v ?? '-' },
    { title: '品牌', dataIndex: 'brand', width: 120, render: (v: string | null) => v ?? '-' },
    { title: 'SKU 数', dataIndex: 'sku_count', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? <Tag color="green">在售</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="产品中心" subtitle="产品与 SKU 分开维护：产品是资料，SKU 才是可报价的最小单位" />

      <SectionCard>
        <div className="toolbar">
          <Input
            // 支持 SKU 编码 / SKU 名称（2026-10-10）。业务员手里拿到的常常是
            // `TP-1210-ST` 这种编码、或者「田字塑料托盘 1200×1000 黑色」这种 SKU 名，
            // 从前只能搜产品名/产品线/品牌，拿编码搜是空结果。
            // 文案一并写出来 —— 能搜什么不写清楚，等于没做。
            placeholder="搜索产品名称 / SKU 编码 / SKU 名称 / 产品线 / 品牌"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 340 }}
            showClear
          />
          <Button
            onClick={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
          >
            查询
          </Button>
          <div style={{ flex: 1 }} />
          {/* 下面那组「产品模板 / SKU模板 / 导出 / 批量导入」整组都要「产品管理」权限
              （后端 io_router 也按同一个权限把守）。没有权限的角色从前是**什么都看不到、
              也什么都不知道** —— 这里补一句常驻灰字：缺哪个权限、找谁开、或者请谁代做。
              为什么不做成一排灰按钮：这一组是四个控件加一个下拉（导入产品/导入SKU），
              全灰着会把工具栏挤成两行，反而盖住真正能用的「查询」；一句话说得清的事
              不必占五个控件的位置。灰字本身可点，点了弹同一句话（点得动）。
              ⚠️ 判据没动：那组控件仍然只有 can('product:manage') 才渲染。 */}
          {!can('product:manage') && (
            <div
              data-blocked-click="1"
              className="blocked-hint-click"
              onClick={notifyManageBlocked}
              style={{ display: 'block', maxWidth: 380, fontSize: 12, lineHeight: 1.7, color: 'var(--crm-text-3)' }}
            >
              {PRODUCT_MANAGE_PERMISSION_REASON}
            </div>
          )}
          {can('product:manage') && (
            <>
              <Button onClick={() => downloadCsv('/api/v1/products/import-template', '产品导入模板.csv')}>
                产品模板
              </Button>
              <Button onClick={() => downloadCsv('/api/v1/skus/import-template', 'SKU导入模板.csv')}>
                SKU模板
              </Button>
              <Button
                onClick={() => downloadCsv('/api/v1/products/export', '产品导出.csv')}
              >
                导出
              </Button>
              <Select
                value={importKind}
                onChange={(v) => setImportKind(v as 'products' | 'skus')}
                style={{ width: 110 }}
                optionList={[
                  { value: 'products', label: '导入产品' },
                  { value: 'skus', label: '导入SKU' },
                ]}
              />
              <Button loading={importing} onClick={() => fileInputRef.current?.click()}>
                批量导入
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) void handleImport(file)
                }}
              />
            </>
          )}
          {can('product:manage') && (
            <Button theme="solid" onClick={() => setModalVisible(true)}>
              新建产品
            </Button>
          )}
        </div>

        <Table<Product>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          empty={emptyText(query, '还没有产品资料')}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: true,
            onPageChange: (next: number) => setPage(next),
            onPageSizeChange: (size: number) => {
              setPageSize(size)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      <Modal
        title="新建产品"
        visible={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('产品名称必填')
            return
          }
          createMutation.mutate(form)
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>产品名称</FormLabel>
            <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>产品线</div>
              <Input
                value={form.product_line ?? ''}
                onChange={(v) => setForm({ ...form, product_line: v })}
                placeholder="塑料制品"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>分类</div>
              <Select
                value={form.category || undefined}
                onChange={(v) => setForm({ ...form, category: String(v) })}
                optionList={(categoriesQuery.data ?? []).map((item) => ({ value: item.code, label: item.label }))}
                placeholder="选择产品分类"
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>品牌</div>
            <Input value={form.brand ?? ''} onChange={(v) => setForm({ ...form, brand: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>销售说明</div>
            <Input
              value={form.description ?? ''}
              onChange={(v) => setForm({ ...form, description: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title="导入结果"
        visible={Boolean(importResult)}
        onCancel={() => setImportResult(null)}
        onOk={() => setImportResult(null)}
        okText="知道了"
        cancelText="关闭"
        footer={null}
        width={620}
      >
        {importResult && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">成功 {importResult.created_count} 条</span>
              {importResult.updated_count ? (
                <span className="chip chip-primary">更新 {importResult.updated_count} 条</span>
              ) : null}
              <span className="chip chip-warning">
                跳过/失败 {importResult.skipped_count + (importResult.failed?.length ?? 0)} 条
              </span>
            </div>
            {importResult.failed?.length > 0 && (
              <div style={{ maxHeight: 260, overflow: 'auto' }}>
                {importResult.failed.map((row, index) => (
                  <div key={index} style={{ marginBottom: 6 }}>
                    <Tag color="red" style={{ marginRight: 8 }}>
                      {row.name}
                    </Tag>
                    {row.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
