/**
 * 回收站。
 *
 * 三块集中在一个页面（主人 2026-10-07 定的口径）：
 *   - 线索：可恢复（复用线索模块现成的删/恢复）
 *   - 产品 / SKU：可恢复。恢复产品会把它名下被删的 SKU **一起**捡回来
 *   - 客户：**只读**。列出被删的客户，被合并掉的标出"已并入某某"并给跳转，
 *     不给恢复按钮 —— 合并怎么还原是留痕快照的事，不在这里做
 *
 * 每个分区的数据各自分页、各自刷新，互不干扰；没有权限的分区直接不显示。
 */

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import { emptyText } from '../../shared/hooks/emptyText'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { usePermissions } from '../../shared/hooks/permissions'
import {
  listRecycleCustomers,
  listRecycleLeads,
  listRecycleProducts,
  listRecycleSkus,
  restoreLead,
  restoreProduct,
  restoreSku,
  type RecycleCustomer,
  type RecycleLead,
  type RecycleProduct,
  type RecycleSku,
} from '../../shared/api/recycle'

const fmt = (value: string | null) => (value ? new Date(value).toLocaleString('zh-CN') : '-')

/** 分页参数：四个分区共用一套写法，各存各的页码。 */
function usePageSize(initial = 10) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(initial)
  return {
    page,
    pageSize,
    setPage,
    // 改每页条数要回到第一页，否则可能停在一个不存在的页码上（表格空）
    changePageSize: (size: number) => {
      setPageSize(size)
      setPage(1)
    },
  }
}

function LeadsPanel() {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const pager = usePageSize()

  const query = useQuery({
    queryKey: ['recycle', 'leads', pager.page, pager.pageSize],
    queryFn: () => listRecycleLeads({ page: pager.page, page_size: pager.pageSize }),
  })

  const restoreMutation = useMutation({
    mutationFn: (id: number) => restoreLead(id),
    onSuccess: () => {
      Toast.success('线索已恢复')
      // 恢复之后它就该回到线索列表里 —— 两个列表都要刷
      void queryClient.invalidateQueries({ queryKey: ['recycle', 'leads'] })
      void queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    { title: '线索名称', dataIndex: 'name' },
    { title: '公司', dataIndex: 'company_name', render: (v: string | null) => v ?? '-' },
    { title: '联系人', dataIndex: 'contact_name', render: (v: string | null) => v ?? '-' },
    { title: '手机', dataIndex: 'mobile', render: (v: string | null) => v ?? '-' },
    { title: '负责人', dataIndex: 'owner_name', width: 110, render: (v: string | null) => v ?? '未分配' },
    {
      title: '原状态',
      dataIndex: 'status_label',
      width: 100,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
    ...(can('lead:assign')
      ? [
          {
            title: '操作',
            width: 90,
            render: (_: unknown, record: RecycleLead) => (
              <a onClick={() => restoreMutation.mutate(record.id)}>恢复</a>
            ),
          },
        ]
      : []),
  ]

  return (
    <SectionCard>
      <Table<RecycleLead>
        columns={columns}
        dataSource={query.data?.items ?? []}
        loading={query.isLoading}
        rowKey="id"
        empty={emptyText(query, '回收站里没有线索')}
        pagination={{
          currentPage: pager.page,
          pageSize: pager.pageSize,
          total: query.data?.total ?? 0,
          showSizeChanger: true,
          onPageChange: pager.setPage,
          onPageSizeChange: pager.changePageSize,
        }}
      />
    </SectionCard>
  )
}

function ProductsPanel() {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const canRestore = can('product:manage')

  const productPager = usePageSize()
  const skuPager = usePageSize()

  const productQuery = useQuery({
    queryKey: ['recycle', 'products', productPager.page, productPager.pageSize],
    queryFn: () => listRecycleProducts({ page: productPager.page, page_size: productPager.pageSize }),
  })
  const skuQuery = useQuery({
    queryKey: ['recycle', 'skus', skuPager.page, skuPager.pageSize],
    queryFn: () => listRecycleSkus({ page: skuPager.page, page_size: skuPager.pageSize }),
  })

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['recycle', 'products'] })
    void queryClient.invalidateQueries({ queryKey: ['recycle', 'skus'] })
    void queryClient.invalidateQueries({ queryKey: ['products'] })
    void queryClient.invalidateQueries({ queryKey: ['skus'] })
  }

  const restoreProductMutation = useMutation({
    mutationFn: (id: number) => restoreProduct(id),
    onSuccess: (result) => {
      const restored = result.restored_skus.length
      const skipped = result.skipped_skus.length
      if (skipped > 0) {
        // 有被跳过的就如实说清楚，别只说"已恢复"让人以为全好了
        Toast.warning(
          `产品已恢复，连带恢复 ${restored} 个 SKU；有 ${skipped} 个因编码被占用未恢复`,
        )
      } else {
        Toast.success(restored > 0 ? `产品已恢复，连带恢复 ${restored} 个 SKU` : '产品已恢复')
      }
      invalidateAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const restoreSkuMutation = useMutation({
    mutationFn: (id: number) => restoreSku(id),
    onSuccess: () => {
      Toast.success('SKU 已恢复')
      invalidateAll()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const productColumns = [
    { title: '产品名称', dataIndex: 'name' },
    { title: '产品线', dataIndex: 'product_line', width: 130, render: (v: string | null) => v ?? '-' },
    { title: '分类', dataIndex: 'category', width: 110, render: (v: string | null) => v ?? '-' },
    { title: '品牌', dataIndex: 'brand', width: 110, render: (v: string | null) => v ?? '-' },
    {
      title: '附带 SKU',
      dataIndex: 'deleted_sku_count',
      width: 110,
      // 恢复这个产品时会一并捡回来的 SKU 条数，先让人心里有数
      render: (count: number) => (count > 0 ? `${count} 个将一起恢复` : '无'),
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
    ...(canRestore
      ? [
          {
            title: '操作',
            width: 90,
            render: (_: unknown, record: RecycleProduct) => (
              <a onClick={() => restoreProductMutation.mutate(record.id)}>恢复</a>
            ),
          },
        ]
      : []),
  ]

  const skuColumns = [
    { title: 'SKU 编码', dataIndex: 'sku_code', width: 160 },
    { title: '名称', dataIndex: 'name', render: (v: string | null) => v ?? '-' },
    { title: '规格', dataIndex: 'specification', width: 150, render: (v: string | null) => v ?? '-' },
    { title: '所属产品', dataIndex: 'product_name', render: (v: string | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'product_deleted',
      width: 170,
      render: (productDeleted: boolean, record: RecycleSku) => {
        if (record.code_occupied) return <Tag color="red">编码被占用</Tag>
        if (productDeleted) return <Tag color="orange">随产品一起恢复</Tag>
        return <Tag>可单独恢复</Tag>
      },
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
    ...(canRestore
      ? [
          {
            title: '操作',
            width: 90,
            render: (_: unknown, record: RecycleSku) =>
              record.product_deleted ? (
                // 产品还在回收站时单独恢复 SKU 会造出孤儿，这里不给按钮
                <span style={{ color: 'var(--crm-text-3)' }}>—</span>
              ) : (
                <a onClick={() => restoreSkuMutation.mutate(record.id)}>恢复</a>
              ),
          },
        ]
      : []),
  ]

  return (
    <>
      <SectionCard title="已删除的产品（恢复时连带恢复它名下的 SKU）">
        <Table<RecycleProduct>
          columns={productColumns}
          dataSource={productQuery.data?.items ?? []}
          loading={productQuery.isLoading}
          rowKey="id"
          empty={emptyText(productQuery, '回收站里没有产品')}
          pagination={{
            currentPage: productPager.page,
            pageSize: productPager.pageSize,
            total: productQuery.data?.total ?? 0,
            showSizeChanger: true,
            onPageChange: productPager.setPage,
            onPageSizeChange: productPager.changePageSize,
          }}
        />
      </SectionCard>
      <SectionCard title="已删除的 SKU">
        <Table<RecycleSku>
          columns={skuColumns}
          dataSource={skuQuery.data?.items ?? []}
          loading={skuQuery.isLoading}
          rowKey="id"
          empty={emptyText(skuQuery, '回收站里没有 SKU')}
          pagination={{
            currentPage: skuPager.page,
            pageSize: skuPager.pageSize,
            total: skuQuery.data?.total ?? 0,
            showSizeChanger: true,
            onPageChange: skuPager.setPage,
            onPageSizeChange: skuPager.changePageSize,
          }}
        />
      </SectionCard>
    </>
  )
}

function CustomersPanel() {
  const navigate = useNavigate()
  const pager = usePageSize()

  const query = useQuery({
    queryKey: ['recycle', 'customers', pager.page, pager.pageSize],
    queryFn: () => listRecycleCustomers({ page: pager.page, page_size: pager.pageSize }),
  })

  const columns = [
    { title: '客户名称', dataIndex: 'name' },
    { title: '简称', dataIndex: 'short_name', width: 140, render: (v: string | null) => v ?? '-' },
    {
      title: '原负责人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v ?? '未分配',
    },
    {
      title: '去向',
      dataIndex: 'merged_into',
      width: 260,
      render: (merged: RecycleCustomer['merged_into']) =>
        merged ? (
          // 只给"跳到合并后的客户"，不给恢复按钮 —— 客户这块主人定的是只做"看"
          <span>
            已并入{' '}
            <a onClick={() => navigate(`/customers/${merged.id}`)}>{merged.name ?? `#${merged.id}`}</a>
          </span>
        ) : (
          <Tag>直接删除</Tag>
        ),
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
  ]

  return (
    <SectionCard title="被合并掉的客户会显示「已并入某某」，可点进合并后的客户；这里不提供恢复。">
      <Table<RecycleCustomer>
        columns={columns}
        dataSource={query.data?.items ?? []}
        loading={query.isLoading}
        rowKey="id"
        empty={emptyText(query, '回收站里没有客户')}
        pagination={{
          currentPage: pager.page,
          pageSize: pager.pageSize,
          total: query.data?.total ?? 0,
          showSizeChanger: true,
          onPageChange: pager.setPage,
          onPageSizeChange: pager.changePageSize,
        }}
      />
    </SectionCard>
  )
}

export default function RecycleBinPage() {
  const { can } = usePermissions()

  // 只把有权限的分区放进 Tab（没权限的接口会 403，不如不显示）
  const tabs = useMemo(
    () =>
      [
        can('lead:view') ? { tab: '线索', itemKey: 'leads' } : null,
        can('product:view') ? { tab: '产品', itemKey: 'products' } : null,
        can('customer:view') ? { tab: '客户', itemKey: 'customers' } : null,
      ].filter((item): item is { itemKey: string; tab: string } => item !== null),
    [can],
  )

  const [picked, setPicked] = useState<string | null>(null)
  // 默认停在第一个有权限的分区；用户点过之后就用他点的那个
  const activeKey = picked ?? tabs[0]?.itemKey ?? ''

  return (
    <div className="page-container">
      <PageHeader
        title="回收站"
        subtitle="这里只放「被删掉的东西」，可以捡回来；客户只做查看"
      />
      {tabs.length === 0 ? (
        <SectionCard>你没有查看任何回收站分区的权限。</SectionCard>
      ) : (
        <SectionCard>
          <Tabs type="line" activeKey={activeKey} onChange={(key) => setPicked(key)} tabList={tabs} />
        </SectionCard>
      )}
      {activeKey === 'leads' && <LeadsPanel />}
      {activeKey === 'products' && <ProductsPanel />}
      {activeKey === 'customers' && <CustomersPanel />}
    </div>
  )
}
