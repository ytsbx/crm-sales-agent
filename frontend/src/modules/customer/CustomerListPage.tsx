import { useRef, useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { createCustomer, listCustomers, type CustomerPayload } from '../../shared/api/customer'
import { useAuthStore } from '../../shared/store/auth'
import type { Customer } from '../../shared/types'

type Scope = 'mine' | 'pool' | 'all'

const SCOPE_OPTIONS = [
  { value: 'mine', label: '我负责的' },
  { value: 'pool', label: '公海客户' },
  { value: 'all', label: '全部客户' },
]

const LEVEL_OPTIONS = [
  { value: 'A', label: 'A 级' },
  { value: 'B', label: 'B 级' },
  { value: 'C', label: 'C 级' },
  { value: 'D', label: 'D 级' },
]

const SOURCE_OPTIONS = ['展会', '官网', '老客户介绍', '企业微信', 'Excel 导入', '手工录入'].map(
  (value) => ({ value, label: value }),
)

const EMPTY_FORM: CustomerPayload = {
  name: '',
  short_name: '',
  region: '',
  address: '',
  source: '手工录入',
  level: 'C',
  remark: '',
}

export default function CustomerListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const currentUser = useAuthStore((state) => state.user)

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState<string | undefined>()
  // 默认范围跟着数据权限走：管理员看全部，业务员看自己的——
  // 否则管理员打开客户中心会是一片空白（他名下本来就没有客户）
  const [scope, setScope] = useState<Scope>(
    currentUser?.data_scope === 'all' ? 'all' : 'mine',
  )
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const [modalVisible, setModalVisible] = useState(false)
  const [form, setForm] = useState<CustomerPayload>(EMPTY_FORM)
  // 批量导入导出
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<{
    created_count: number
    skipped_count: number
    failed_count: number
    created: Array<{ name: string }>
    skipped: Array<{ name: string; reason: string }>
    failed: Array<{ name: string; reason: string }>
  } | null>(null)

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
      const response = await axios.post('/api/v1/customers/import', formData, {
        headers: tokenHeader(),
      })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message)
      Toast.success(body.message)
      setImportResult(body.data)
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const query = useQuery({
    queryKey: ['customers', { keyword, level, scope, page, pageSize, userId: currentUser?.id }],
    queryFn: () =>
      listCustomers({
        keyword,
        level,
        page,
        page_size: pageSize,
        owner_id: scope === 'mine' ? currentUser?.id : undefined,
        pool_status: scope === 'pool' ? 'public' : undefined,
      }),
  })

  const createMutation = useMutation({
    mutationFn: (payload: CustomerPayload) => createCustomer(payload),
    onSuccess: (customer) => {
      Toast.success(`客户「${customer.name}」已创建`)
      setModalVisible(false)
      setForm(EMPTY_FORM)
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['workbench'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const handleSearch = () => {
    setKeyword(keywordInput.trim())
    setPage(1)
  }

  const columns = [
    {
      title: '客户名称',
      dataIndex: 'name',
      render: (text: string, record: Customer) => (
        <a style={{ color: 'var(--crm-primary)' }} onClick={() => navigate(`/customers/${record.id}`)}>
          {text}
        </a>
      ),
    },
    {
      title: '等级',
      dataIndex: 'level',
      width: 80,
      render: (value: string | null) =>
        value ? (
          <Tag color={value === 'A' ? 'green' : value === 'B' ? 'blue' : 'grey'}>{value}</Tag>
        ) : (
          '-'
        ),
    },
    { title: '地区', dataIndex: 'region', width: 100, render: (v: string | null) => v ?? '-' },
    { title: '来源', dataIndex: 'source', width: 120, render: (v: string | null) => v ?? '-' },
    {
      title: '联系人',
      dataIndex: 'contact_count',
      width: 90,
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v ?? '公海',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? '正常' : v),
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
      <PageHeader title="客户中心" subtitle="客户、联系人与归属都在这里统一管理" />

      <div className="card-block">
        <div className="toolbar">
          <Input
            placeholder="搜索客户名称 / 简称 / 地址"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={handleSearch}
            style={{ width: 260 }}
            showClear
          />
          <Select
            placeholder="客户等级"
            value={level}
            onChange={(value) => {
              setLevel(value as string | undefined)
              setPage(1)
            }}
            optionList={LEVEL_OPTIONS}
            style={{ width: 140 }}
            showClear
          />
          <Select
            value={scope}
            onChange={(value) => {
              setScope(value as Scope)
              setPage(1)
            }}
            optionList={SCOPE_OPTIONS}
            style={{ width: 140 }}
          />
          <Button onClick={handleSearch}>查询</Button>
          <div style={{ flex: 1 }} />
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void handleImport(file)
            }}
          />
          <Button onClick={() => downloadCsv('/api/v1/customers/import-template', '客户导入模板.csv')}>
            下载模板
          </Button>
          <Button onClick={() => downloadCsv('/api/v1/customers/export', '客户列表.csv')}>
            导出
          </Button>
          <Button loading={importing} onClick={() => fileInputRef.current?.click()}>
            批量导入
          </Button>
          <Button theme="solid" onClick={() => setModalVisible(true)}>
            新建客户
          </Button>
        </div>

        <Table<Customer>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty="没有符合条件的客户"
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOpts: [10, 20, 50],
            onPageChange: (nextPage: number) => setPage(nextPage),
            onPageSizeChange: (nextSize: number) => {
              setPageSize(nextSize)
              setPage(1)
            },
          }}
        />
      </div>

      <Modal
        title="导入结果"
        visible={Boolean(importResult)}
        onCancel={() => setImportResult(null)}
        onOk={() => setImportResult(null)}
        okText="知道了"
        cancelText="关闭"
        width={620}
      >
        {importResult && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">成功 {importResult.created_count} 条</span>
              <span className="chip chip-warning">
                跳过疑似重复 {importResult.skipped_count} 条
              </span>
              {importResult.failed_count > 0 && (
                <span className="chip chip-error">失败 {importResult.failed_count} 条</span>
              )}
            </div>
            {importResult.skipped.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>被跳过的行</div>
                {importResult.skipped.map((item) => (
                  <div
                    key={`skip-${item.name}`}
                    style={{ fontSize: 12, color: 'var(--crm-text-2)', lineHeight: 1.8 }}
                  >
                    {item.name} —— {item.reason}
                  </div>
                ))}
              </div>
            )}
            {importResult.failed.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>失败的行</div>
                {importResult.failed.map((item) => (
                  <div
                    key={`fail-${item.name}`}
                    style={{ fontSize: 12, color: 'var(--crm-error)', lineHeight: 1.8 }}
                  >
                    {item.name} —— {item.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        title="新建客户"
        visible={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('客户名称必填')
            return
          }
          createMutation.mutate(form)
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>客户名称 *</div>
            <Input
              value={form.name}
              onChange={(value) => setForm({ ...form, name: value })}
              placeholder="公司全称，例如：宁波宏远包装制品有限公司"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户简称</div>
            <Input
              value={form.short_name ?? ''}
              onChange={(value) => setForm({ ...form, short_name: value })}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>省份 / 地区</div>
              <Input
                value={form.region ?? ''}
                onChange={(value) => setForm({ ...form, region: value })}
                placeholder="浙江"
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>客户等级</div>
              <Select
                value={form.level ?? undefined}
                onChange={(value) => setForm({ ...form, level: value as string })}
                optionList={LEVEL_OPTIONS}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户来源</div>
            <Select
              value={form.source ?? undefined}
              onChange={(value) => setForm({ ...form, source: value as string })}
              optionList={SOURCE_OPTIONS}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>详细地址</div>
            <Input
              value={form.address ?? ''}
              onChange={(value) => setForm({ ...form, address: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <Input
              value={form.remark ?? ''}
              onChange={(value) => setForm({ ...form, remark: value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
