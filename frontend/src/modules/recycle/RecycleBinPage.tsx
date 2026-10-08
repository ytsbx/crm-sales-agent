/**
 * 回收站。
 *
 * 三块集中在一个页面（主人 2026-10-07 定的口径）：
 *   - 线索：可恢复（复用线索模块现成的删/恢复）
 *   - 产品 / SKU：可恢复。恢复产品会把它名下被删的 SKU **一起**捡回来
 *   - 客户：**只有"直接删除"的能恢复**（2026-10-08 加）。被合并掉的标出
 *     "已并入某某"并给跳转，**不给恢复按钮** —— 它名下已经被搬空，恢复只会得到
 *     一个空壳，还会把"已经被合并"这个事实盖掉。这两类靠 `removed_via` 分。
 *
 * 每个分区的数据各自分页、各自刷新，互不干扰；没有权限的分区直接不显示。
 */

import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Modal, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import { emptyText } from '../../shared/hooks/emptyText'
import FormLabel from '../../shared/components/FormLabel'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { listUsers } from '../../shared/api/system'
import { usePermissions } from '../../shared/hooks/permissions'
import {
  isOwnerRequiredError,
  listRecycleCustomers,
  listRecycleLeads,
  listRecycleProducts,
  listRecycleSkus,
  restoreCustomer,
  restoreLead,
  restoreProduct,
  restoreSku,
  type MergeRef,
  type RecycleCustomer,
  type RecycleLead,
  type RecycleProduct,
  type RecycleSku,
  type RemovedVia,
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

/**
 * 把当前页钳到有效范围内。
 *
 * 为什么必须做：**恢复**会让总数变小。停在最后一页时恢复掉那页唯一的记录，
 * 页码就会落到一个已经不存在的位置上 —— 界面显示"回收站里没有线索"，
 * 右上角却又写着"共 10 条"，看着像数据坏了（实际只是页码没跟上）。
 *
 * 放在"数据刷新之后"做（依赖 total），而不是恢复成功那一刻：那一刻拿不到
 * 新的总数。产品恢复会同时缩短产品、SKU 两张表的记录，所以两张表都要各自钳一次。
 */
function useClampPage(
  total: number | undefined,
  page: number,
  pageSize: number,
  setPage: (page: number) => void,
) {
  useEffect(() => {
    if (total === undefined) return
    const lastPage = Math.max(1, Math.ceil(total / pageSize))
    if (page > lastPage) setPage(lastPage)
  }, [total, page, pageSize, setPage])
}

/** 去向里"打不开的那些情况"该显示什么 —— 别让用户对着空白猜。 */
const MERGE_STATE_TEXT: Record<MergeRef['state'], string> = {
  ok: '—',
  forbidden: '合并目标无查看权限',
  gone: '合并目标已不存在',
  loop: '合并去向异常',
  truncated: '合并链过长，最终去向待核实',
}

/** 链没走通的两种状态：没有可跳转的目标，说明文案本身已经是一句完整的话。 */
const CHAIN_UNRESOLVED: ReadonlyArray<MergeRef['state']> = ['loop', 'truncated']

const HINT: CSSProperties = { color: 'var(--crm-text-3)' }

/**
 * 「这条是怎么没的」的文案。
 *
 * 客户和 SKU 各有自己的两种来源，**同一个 `direct` 在两边叫法不同**
 * （客户是"直接删除"、SKU 是"单独删除"），所以各配一张表，别共用一张。
 */
const CUSTOMER_REMOVED_TEXT: Record<RemovedVia, string> = {
  direct: '直接删除',
  merged: '被合并移除',
  with_product: '—',
}

const SKU_REMOVED_TEXT: Record<RemovedVia, string> = {
  direct: '单独删除',
  with_product: '随产品删除',
  merged: '—',
}

/**
 * 「谁删的」那一栏的统一渲染。
 *
 * 留痕里没有操作人就如实说「历史操作人待核实」——**不拿负责人顶替**：
 * 负责人说的是"这客户归谁管"，跟"谁删的"是两件事，混起来会让人找错人。
 */
const renderDeletedBy = (name: string | null, pending: boolean) =>
  pending ? <span style={HINT}>历史操作人待核实</span> : (name ?? '—')

function LeadsPanel() {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const pager = usePageSize()

  const query = useQuery({
    queryKey: ['recycle', 'leads', pager.page, pager.pageSize],
    queryFn: () => listRecycleLeads({ page: pager.page, page_size: pager.pageSize }),
  })
  // 恢复会让总数变小，停在最后一页时要把页码拉回来（见 useClampPage）
  useClampPage(query.data?.total, pager.page, pager.pageSize, pager.setPage)

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
    {
      // 线索只可能是被人直接删的，所以没有"删除方式"那一栏 —— 谁删的还是要看
      title: '删除人',
      dataIndex: 'deleted_by_name',
      width: 150,
      render: (v: string | null, record: RecycleLead) =>
        renderDeletedBy(v, record.deleted_by_pending),
    },
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
  // 产品恢复会**同时**缩短产品和 SKU 两张表的记录，两张表各自钳一次页码
  useClampPage(productQuery.data?.total, productPager.page, productPager.pageSize, productPager.setPage)
  useClampPage(skuQuery.data?.total, skuPager.page, skuPager.pageSize, skuPager.setPage)

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
    {
      // 产品也只可能是被人直接删的，同样不需要"删除方式"那一栏
      title: '删除人',
      dataIndex: 'deleted_by_name',
      width: 150,
      render: (v: string | null, record: RecycleProduct) =>
        renderDeletedBy(v, record.deleted_by_pending),
    },
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
    {
      // 「随产品删除」标成橙色：它直接决定下一步该去恢复**产品**，
      // 而不是对着这条 SKU 点恢复（那样只会得到一句"请先恢复产品"）
      title: '删除方式',
      dataIndex: 'removed_via',
      width: 150,
      render: (via: RemovedVia | null) => {
        // 判不出来就说"待核实"，不硬安一个方式（历史数据常是这样）
        if (via === null) return <Tag>待核实</Tag>
        return via === 'with_product' ? (
          <Tag color="orange">{SKU_REMOVED_TEXT[via]}</Tag>
        ) : (
          <Tag>{SKU_REMOVED_TEXT[via]}</Tag>
        )
      },
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
    {
      // 「随产品删除」时这里给的是**删产品的那个人** —— 正是"谁把它带走的"
      title: '删除人',
      dataIndex: 'deleted_by_name',
      width: 150,
      render: (v: string | null, record: RecycleSku) =>
        renderDeletedBy(v, record.deleted_by_pending),
    },
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
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  // 恢复与删除**共用同一把钥匙**（后端也是这么定的）：谁删的谁能拾回来，
  // 不为它单开权限码 —— 与线索、产品那两个模块的恢复一致
  const canRestore = can('customer:delete')
  // 「恢复时把客户交给别人」= 一次改派，后端**额外**要分配权限（主人口径 2026-10-08）。
  // 界面跟着同一口径给入口：没这个权限就别把选人框弹出来让人白填一遍。
  const canAssign = can('customer:assign')

  const query = useQuery({
    queryKey: ['recycle', 'customers', pager.page, pager.pageSize],
    queryFn: () => listRecycleCustomers({ page: pager.page, page_size: pager.pageSize }),
  })
  useClampPage(query.data?.total, pager.page, pager.pageSize, pager.setPage)

  // 「指定新负责人」弹窗只服务一种情况：**原负责人已停用（或账号没了）** ——
  // 这种客户不指定人就恢复不出来（后端拿 `OWNER_REQUIRED_CODE` 拦）。
  // 其余情况点「恢复」是**一步到位**的，不该多弹一层。
  const [ownerPicker, setOwnerPicker] = useState<RecycleCustomer | null>(null)
  const [pickedOwnerId, setPickedOwnerId] = useState<number | null>(null)
  const usersQuery = useQuery({
    queryKey: ['assignable-users', 'active'],
    queryFn: () => listUsers({ status: 'active', page: 1, page_size: 200 }),
    // 真要选人时才去拉名单 —— 这条接口本身要「分配客户」或「用户管理」权限
    enabled: ownerPicker !== null && canAssign,
  })

  const closeOwnerPicker = () => {
    setOwnerPicker(null)
    setPickedOwnerId(null)
  }

  const restoreMutation = useMutation({
    // `ownerId` 只在弹窗里选人时才有值；两个入口共用一次提交与一套刷新
    mutationFn: (vars: { id: number; ownerId?: number | null }) =>
      restoreCustomer(vars.id, vars.ownerId ?? null),
    onSuccess: () => {
      Toast.success('客户已恢复')
      closeOwnerPicker()
      void queryClient.invalidateQueries({ queryKey: ['recycle', 'customers'] })
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
    },
    onError: (error: Error, vars) => {
      // 「还没指定新负责人」不是失败，是**这一步还没做完**：把选人框弹出来接着做。
      // 判据认后端给的**错误标识**，不匹配提示文字（文案改了也不会悄悄失效）。
      if (isOwnerRequiredError(error)) {
        if (vars.ownerId != null) {
          // 人已经选过了 → **把他的选择留着**（弹窗不关、选中项不清），只提示一句
          Toast.warning(error.message)
          return
        }
        const target = (query.data?.items ?? []).find((row) => row.id === vars.id)
        if (target && canAssign) {
          setPickedOwnerId(null)
          setOwnerPicker(target)
          Toast.warning(error.message)
          return
        }
      }
      // 其余失败（被合并掉的、没权限、选到的人刚被停用……）原样显示后端那句话，
      // 别把它盖成一句"操作失败"
      Toast.error(error.message)
    },
  })

  /** 点「恢复」：原负责人不能接手就先选人，否则直接恢复。 */
  const startRestore = (record: RecycleCustomer) => {
    if (record.original_owner_active === false) {
      if (!canAssign) {
        // 换负责人要分配权限，硬点也是 403 —— 直接说清去找谁，别让人白点一次
        Toast.warning(
          '这条客户的原负责人已停用，得另指定一位在职的负责人；' +
            '请找有「分配客户」权限的同事处理',
        )
        return
      }
      setPickedOwnerId(null)
      setOwnerPicker(record)
      return
    }
    restoreMutation.mutate({ id: record.id })
  }

  const submitOwnerPick = () => {
    if (!ownerPicker) return
    if (pickedOwnerId == null) {
      Toast.warning('请选择一位在职的新负责人')
      return
    }
    restoreMutation.mutate({ id: ownerPicker.id, ownerId: pickedOwnerId })
  }

  /**
   * 渲染一个「去向」：能打开就给链接，打不开就说明为什么。
   *
   * `name` 可能为空 —— 目标客户不在数据范围内时后端**刻意不下发名字**，
   * 这时候绝不能拿它拼一句"已并入 null"。同理 `id` 为空就不给链接，
   * 免得用户点进一个 404 页面。
   */
  const renderTarget = (ref: MergeRef) => {
    if (ref.id != null) {
      return <a onClick={() => navigate(`/customers/${ref.id}`)}>{ref.name ?? `#${ref.id}`}</a>
    }
    if (ref.name) return <span>{ref.name}</span>
    return <span style={{ color: 'var(--crm-text-3)' }}>{MERGE_STATE_TEXT[ref.state]}</span>
  }

  const columns = [
    { title: '客户名称', dataIndex: 'name' },
    { title: '简称', dataIndex: 'short_name', width: 140, render: (v: string | null) => v ?? '-' },
    {
      title: '原负责人',
      dataIndex: 'original_owner_name',
      width: 160,
      // "待核实" ≠ "未分配"：前者是合并留痕里没记当时的负责人（只有管理员看得到），
      // 后者是这条客户本来就没有负责人。说成一样的会把人往错的方向带。
      render: (v: string | null, record: RecycleCustomer) => {
        if (record.owner_pending) return '待核实'
        if (!v) return '未分配'
        // 已停用（或账号已不存在）：这条客户恢复时**必须先指定新负责人**。
        // 提前标出来 —— 用户点之前就知道会弹选人框，而不是被拒了才知道。
        if (record.original_owner_active === false) {
          return (
            <span>
              {v}
              <span style={{ color: 'var(--crm-text-3)', marginLeft: 4 }}>已停用</span>
            </span>
          )
        }
        return v
      },
    },
    {
      // 客户有两种"没掉"的方式：有人直接删的 / 被合并掉的。从前只能从「去向」那一栏
      // 去猜 —— 现在直接说明。两栏分工：这一栏说**动作**，那一栏说**结果**。
      title: '操作方式',
      dataIndex: 'removed_via',
      width: 130,
      render: (via: RemovedVia) =>
        via === 'merged' ? (
          <Tag color="orange">{CUSTOMER_REMOVED_TEXT[via]}</Tag>
        ) : (
          <Tag>{CUSTOMER_REMOVED_TEXT[via]}</Tag>
        ),
    },
    {
      title: '去向',
      dataIndex: 'merged_into',
      width: 280,
      render: (merged: RecycleCustomer['merged_into'], record: RecycleCustomer) => {
        // 没有去向＝没人接手。操作方式那一栏已经说了"直接删除"，这里不再重复一遍
        if (!merged) return <span style={HINT}>—</span>
        return (
          <div>
            <div>已并入 {renderTarget(merged)}</div>
            {/* A→B→C：B 自己也被并走了。后端只在"最终去处与直接历史不同"时才下发。
                链没走通（成环 / 超过追踪上限）时那句话本身已经说完了去向，不再拼"最终" */}
            {record.final_target ? (
              <div style={{ color: 'var(--crm-text-3)' }}>
                {CHAIN_UNRESOLVED.includes(record.final_target.state)
                  ? MERGE_STATE_TEXT[record.final_target.state]
                  : <>最终 {renderTarget(record.final_target)}</>}
              </div>
            ) : null}
          </div>
        )
      },
    },
    {
      // 合并原因：只有被合并掉的才有；直接删除的为空 —— 显示成"—"而不是一个空框
      title: '合并原因',
      dataIndex: 'merge_reason',
      width: 200,
      render: (v: string | null) => v || <span style={HINT}>—</span>,
    },
    { title: '删除时间', dataIndex: 'deleted_at', width: 170, render: fmt },
    {
      // 合并来源给的是**合并留痕里的操作人**（谁把这条并进了哪条），
      // 直接删除的取流水账；两者都查不到时说"待核实"，不拿负责人顶替
      title: '删除人',
      dataIndex: 'deleted_by_name',
      width: 150,
      render: (v: string | null, record: RecycleCustomer) =>
        renderDeletedBy(v, record.deleted_by_pending),
    },
    ...(canRestore
      ? [
          {
            title: '操作',
            width: 90,
            render: (_: unknown, record: RecycleCustomer) =>
              record.removed_via === 'direct' ? (
                // 原负责人已停用的那条会先弹选人框（`startRestore` 里分派）
                <a onClick={() => startRestore(record)}>恢复</a>
              ) : (
                // 被合并掉的**不给**恢复按钮：它名下已经被搬空，恢复只会得到空壳，
                // 还会把"已经被合并"这个事实盖掉。去向那一栏本来就有跳转。
                <span style={{ color: 'var(--crm-text-3)' }}>—</span>
              ),
          },
        ]
      : []),
  ]

  return (
    <>
      <SectionCard title="被合并掉的客户会显示「已并入某某」，可点进合并后的客户（那一类不提供恢复）；直接删除的可以恢复。原负责人已停用的，恢复时要先指定一位在职的接手人。">
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
      <Modal
        title="指定新负责人"
        visible={ownerPicker !== null}
        // 取消不清"已经选过的人"以外的东西；选项本身在这里清掉是安全的
        onCancel={closeOwnerPicker}
        onOk={submitOwnerPick}
        confirmLoading={restoreMutation.isPending}
        okText="恢复并交给这位"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--crm-text-2)', lineHeight: 1.7 }}>
            客户「{ownerPicker?.name}」的原负责人
            {ownerPicker?.original_owner_name ? `（${ownerPicker.original_owner_name}）` : ''}
            已停用，照原样恢复出来没人能接手。请指定一位在职的同事：
          </div>
          <div>
            <FormLabel required>新负责人</FormLabel>
            <Select
              value={pickedOwnerId ?? undefined}
              onChange={(value) => setPickedOwnerId((value as number | undefined) ?? null)}
              placeholder="从在职同事里选一位"
              style={{ width: '100%' }}
              filter
              loading={usersQuery.isLoading}
              optionList={(usersQuery.data?.items ?? []).map((item) => ({
                label: item.name,
                value: item.id,
              }))}
            />
          </div>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
            恢复后，原本挂在这位原负责人名下的未完成待办、报价、订单等会跟着一起接过去；
            其他同事手里的活、订单的历史业绩归属一个字不动。
          </div>
        </div>
      </Modal>
    </>
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
