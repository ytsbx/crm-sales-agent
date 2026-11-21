import { formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Dropdown, Input, Modal, Table, Toast } from '@douyinfe/semi-ui'

import { createProduct, listProducts, type ProductPayload } from '../../shared/api/product'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Product } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { useBlockedNotify } from '../../shared/components/BlockedHint'
import { getPersistedAuthToken } from '../../shared/auth-token'
import StatusDot from '../../shared/components/StatusDot'
import CsvImportButtons from '../../shared/components/CsvImportButtons'

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
  const [pageSize, setPageSize] = useState(20)
  const [modalVisible, setModalVisible] = useState(false)
  const [importMenuOpen, setImportMenuOpen] = useState(false)
  const [form, setForm] = useState<ProductPayload>(EMPTY_FORM)

  const tokenHeader = () => {
    const token = getPersistedAuthToken()
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

  /* 1024 宽内容区约 700px，产品表七列合计 730px+，「状态 / 创建时间」
     被裁到卡片外。窄屏切紧凑档：列宽收紧，创建时间在详情里能看，列表先不占位。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const columns = [
    {
      title: '产品名称',
      dataIndex: 'name',
      width: compact ? 200 : undefined,
      render: (text: string, record: Product) => (
        <a
          title={text}
          style={{
            color: 'var(--crm-primary)',
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          onClick={() => navigate(`/products/${record.id}`)}
        >
          {text}
        </a>
      ),
    },
    { title: '产品线', dataIndex: 'product_line', width: compact ? 100 : 140, render: (v: string | null) => v ?? '-' },
    { title: '分类', dataIndex: 'category', width: compact ? 100 : 120, render: (v: string | null) => v ?? '-' },
    { title: '品牌', dataIndex: 'brand', width: compact ? 90 : 120, render: (v: string | null) => v ?? '-' },
    { title: 'SKU 数', dataIndex: 'sku_count', width: compact ? 70 : 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: compact ? 70 : 90,
      render: (v: string) => (
        <StatusDot label={v === 'active' ? '在售' : '停用'} tone={v === 'active' ? 'green' : 'grey'} />
      ),
    },
    ...(compact
      ? []
      : [{
          title: '创建时间',
          dataIndex: 'created_at',
          width: 170,
          render: (v: string) => formatBeijingTime(v),
        }]),
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
          {/* 和订单 / 报价 / 客户 / 线索四页对齐：查询是"再筛一次"，不是主操作，
              降成文字按钮，让「新建产品」独占这一行的实心蓝（2026-11-21 巡检）。 */}
          <Button
            type="tertiary"
            theme="borderless"
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
            <CsvImportButtons
              invalidateQueryKeys={['products', 'skus']}
              renderControls={(openImport, importing) => (
                <Dropdown
                  trigger="click"
                  visible={importMenuOpen}
                  onVisibleChange={setImportMenuOpen}
                  position="bottomRight"
                  render={
                    <Dropdown.Menu>
                      <Dropdown.Item onClick={() => void downloadCsv('/api/v1/products/import-template', '产品导入模板.csv')}>
                        下载产品导入模板
                      </Dropdown.Item>
                      <Dropdown.Item onClick={() => void downloadCsv('/api/v1/skus/import-template', 'SKU导入模板.csv')}>
                        下载 SKU 导入模板
                      </Dropdown.Item>
                      <Dropdown.Item onClick={() => void downloadCsv('/api/v1/products/export', '产品导出.csv')}>
                        导出全部产品
                      </Dropdown.Item>
                      <Dropdown.Item onClick={() => {
                        setImportMenuOpen(false)
                        openImport({ url: '/api/v1/products/import', label: '产品' })
                      }}>
                        导入产品
                      </Dropdown.Item>
                      <Dropdown.Item onClick={() => {
                        setImportMenuOpen(false)
                        openImport({
                          url: '/api/v1/skus/import',
                          label: 'SKU',
                          hint: 'SKU 编码可留空，系统会自动生成；重复行按产品和规格跳过。',
                        })
                      }}>
                        导入 SKU
                      </Dropdown.Item>
                    </Dropdown.Menu>
                  }
                >
                  <Button loading={importing} type="tertiary" theme="borderless" style={{ color: 'var(--crm-primary)' }}>
                    导入 / 导出 ▾
                  </Button>
                </Dropdown>
              )}
            />
          )}
          {can('product:manage') && (
            <Button type="primary" theme="outline" onClick={() => setModalVisible(true)}>
              新建产品
            </Button>
          )}
        </div>

        <Table<Product>
          className="products-table"
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
        width={520}
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
              <Input
                value={form.category ?? ''}
                onChange={(v) => setForm({ ...form, category: v })}
                placeholder="自行填写产品分类"
                maxLength={64}
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
