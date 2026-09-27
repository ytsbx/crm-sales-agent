import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { createProduct, listProducts, type ProductPayload } from '../../shared/api/product'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Product } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'

const EMPTY_FORM: ProductPayload = {
  name: '',
  product_line: '',
  category: '',
  brand: '',
  description: '',
}

export default function ProductListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [modalVisible, setModalVisible] = useState(false)
  const [form, setForm] = useState<ProductPayload>(EMPTY_FORM)

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
            placeholder="搜索产品名称 / 产品线 / 品牌"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 260 }}
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
            <div style={{ marginBottom: 4 }}>产品名称 *</div>
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
              <Input
                value={form.category ?? ''}
                onChange={(v) => setForm({ ...form, category: v })}
                placeholder="物流器具"
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
    </div>
  )
}
