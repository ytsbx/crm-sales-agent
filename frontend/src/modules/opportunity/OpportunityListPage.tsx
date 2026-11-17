import { useState } from 'react'
import { parseOptionalNumber } from '../../shared/number-input'
import { calendarDay } from '../../shared/calendar-day'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import { listCustomers } from '../../shared/api/customer'
import {
  createOpportunity,
  getFunnel,
  listOpportunities,
  listStages,
  type Opportunity,
} from '../../shared/api/opportunity'
import { usePermissions } from '../../shared/hooks/permissions'
import { emptyNode } from '../../shared/components/EmptyState'
import OpportunityBoard from './OpportunityBoard'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
import StatusDot from '../../shared/components/StatusDot'

const STATUS_OPTIONS = [
  { value: 'open', label: '进行中' },
  { value: 'win', label: '已成交' },
  { value: 'loss', label: '已失单' },
]

const RISK_LABEL: Record<string, string> = { high: '高风险', medium: '中风险', low: '低风险' }

/** 空值占位：一条淡灰横线。比 '-' 更像"这里本来就没东西"，不会读成减号。 */
function EmptyMark() {
  return <span style={{ color: 'var(--crm-text-3)' }}>-</span>
}

export default function OpportunityListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string>('open')
  const [stageId, setStageId] = useState<number | undefined>()
  const [view, setView] = useState<'table' | 'board'>('table')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState({
    customer_id: null as number | null,
    title: '',
    expected_amount: '',
    expected_close_date: null as Date | null,
    competitor: '',
    next_action: '',
  })

  const stagesQuery = useQuery({ queryKey: ['stages'], queryFn: listStages })
  const funnelQuery = useQuery({ queryKey: ['funnel'], queryFn: getFunnel })
  const customersQuery = useQuery({
    queryKey: ['customers-for-select'],
    queryFn: () => listCustomers({ page: 1, page_size: 100 }),
    enabled: createVisible,
  })

  const query = useQuery({
    queryKey: ['opportunities', { keyword, status, stageId, page, pageSize }],
    queryFn: () =>
      listOpportunities({
        keyword,
        status: status || undefined,
        stage_id: stageId,
        page,
        page_size: pageSize,
      }),
  })

  const createMutation = useMutation({
    mutationFn: () =>
      createOpportunity({
        customer_id: form.customer_id,
        title: form.title,
        // 同类：字母会被静默变成 null（= 清空金额）
        expected_amount: (() => {
          const v = parseOptionalNumber(form.expected_amount)
          if (typeof v === 'number' && Number.isNaN(v)) {
            throw new Error('预计金额只能填数字')
          }
          return v
        })(),
        expected_close_date: form.expected_close_date
          ? calendarDay(form.expected_close_date)
          : null,
        competitor: form.competitor || null,
        next_action: form.next_action || null,
      }),
    onSuccess: (opportunity) => {
      Toast.success('商机已创建')
      setCreateVisible(false)
      setForm({
        customer_id: null,
        title: '',
        expected_amount: '',
        expected_close_date: null,
        competitor: '',
        next_action: '',
      })
      void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
      void queryClient.invalidateQueries({ queryKey: ['funnel'] })
      navigate(`/opportunities/${opportunity.id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    {
      title: '商机名称',
      dataIndex: 'title',
      render: (text: string, record: Opportunity) => (
        <a style={{ color: 'var(--crm-primary)' }} onClick={() => navigate(`/opportunities/${record.id}`)}>
          {text}
        </a>
      ),
    },
    { title: '客户', dataIndex: 'customer_name', width: 220, render: (v: string | null) => v ?? '-' },
    {
      title: '阶段',
      dataIndex: 'stage_name',
      width: 110,
      render: (value: string | null, record: Opportunity) => (
        <StatusDot
          label={value ?? '-'}
          tone={record.status === 'win' ? 'green' : record.status === 'loss' ? 'grey' : 'blue'}
        />
      ),
    },
    // 原来这里是四列：预计金额 / 需求条数 / 预计成交日 / 风险。
    // 演示库里这四列几乎全是 `-` 和 `0`，一行里四个空位并排 ——
    // 视觉噪音比信息多（2026-11-16 巡检）。两两并成一列：
    // 「有值就显示、没值就一条淡灰横线」，空的时候一行只剩一个占位符。
    {
      title: '金额 / 需求',
      width: 260,
      render: (_: unknown, record: Opportunity) => {
        const parts: string[] = []
        if (record.expected_amount) parts.push(`¥${record.expected_amount.toLocaleString('zh-CN')}`)
        if (record.item_count) parts.push(`${record.item_count} 条需求`)
        return parts.length ? parts.join(' · ') : <EmptyMark />
      },
    },
    {
      title: '成交日 / 风险',
      width: 200,
      render: (_: unknown, record: Opportunity) => {
        const parts: string[] = []
        if (record.expected_close_date) parts.push(record.expected_close_date)
        if (record.risk_level) parts.push(RISK_LABEL[record.risk_level] ?? record.risk_level)
        return parts.length ? parts.join(' · ') : <EmptyMark />
      },
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? <EmptyMark /> },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="商机中心"
        subtitle="客户需求逐条录入，后续核价与报价都以需求明细为输入"
      />

      {/* 阶段漏斗：原来这里是 8 张 kpi-card（4+4 两行、每张 155 高），
          每张卡里只有「条数 + 金额」两个数 —— 内容少却占了三百像素，
          主人说的"内容少就别给大卡"就是这个。改成一条 8 格的漏斗条：
          同一个阶段还是同一个位置，数字和金额都在，只是不再各占一张卡。 */}
      <div className="stage-strip">
        {(funnelQuery.data ?? [])
          .filter((row) => row.count > 0 || row.sequence <= 4)
          .map((row) => (
            <div className="stage-tile" key={row.stage_id}>
              <div className="stage-tile-label" title={row.stage_name}>
                {row.stage_name}
              </div>
              <div className="stage-tile-value">{row.count}</div>
              <div className="stage-tile-amount">
                ¥{Math.round(row.amount).toLocaleString('zh-CN')}
              </div>
            </div>
          ))}
      </div>

      <SectionCard>
        <div className="toolbar">
          <Input
            placeholder="搜索商机名称"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 240 }}
            showClear
          />
          <Select
            placeholder="阶段"
            value={stageId}
            onChange={(value) => {
              setStageId(value as number | undefined)
              setPage(1)
            }}
            optionList={(stagesQuery.data ?? []).map((stage) => ({
              value: stage.id,
              label: stage.name,
            }))}
            style={{ width: 140 }}
            showClear
          />
          <Select
            value={status}
            onChange={(value) => {
              setStatus(value as string)
              setPage(1)
            }}
            optionList={STATUS_OPTIONS}
            style={{ width: 130 }}
            disabled={view === 'board'}
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
          <div style={{ display: 'flex', gap: 0, marginRight: 8 }}>
            <Button
              size="small"
              theme={view === 'table' ? 'solid' : 'borderless'}
              onClick={() => setView('table')}
            >
              表格
            </Button>
            <Button
              size="small"
              theme={view === 'board' ? 'solid' : 'borderless'}
              onClick={() => setView('board')}
            >
              看板
            </Button>
          </div>
          {can('opportunity:manage') && (
            <Button theme="solid" onClick={() => setCreateVisible(true)}>
              新建商机
            </Button>
          )}
        </div>

        {view === 'board' ? (
          <OpportunityBoard
            keyword={keyword}
            canManage={can('opportunity:manage')}
            onChanged={() => {
              void queryClient.invalidateQueries({ queryKey: ['opportunity-board'] })
              void queryClient.invalidateQueries({ queryKey: ['funnel'] })
            }}
          />
        ) : (
          <Table<Opportunity>
            columns={columns}
            dataSource={query.data?.items ?? []}
            loading={query.isLoading}
            rowKey="id"
            size="middle"
            empty={emptyNode(query, {
              title: '还没有商机',
              hint: '点右上角「新建商机」，先把客户要什么逐条录进来，后续核价与报价都以它为准。',
            })}
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
        )}
      </SectionCard>

      <Modal
        title="新建商机"
        width={520}
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!form.customer_id) {
            Toast.warning('请选择客户')
            return
          }
          if (!form.title.trim()) {
            Toast.warning('商机名称必填')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>客户</FormLabel>
            <Select
              placeholder="选择客户"
              value={form.customer_id ?? undefined}
              onChange={(value) => setForm({ ...form, customer_id: value as number })}
              optionList={optionsWithCodeOnDuplicate(
                customersQuery.data?.items ?? [],
                (item) => item.name,
                (item) => item.id,
              )}
              loading={customersQuery.isLoading}
              filter={optionMatcher}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <FormLabel required>商机名称</FormLabel>
            <Input
              value={form.title}
              onChange={(v) => setForm({ ...form, title: v })}
              placeholder="例如：周转箱年度采购"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>预计金额（元）</div>
              <Input
                value={form.expected_amount}
                onChange={(v) => setForm({ ...form, expected_amount: v })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>预计成交日期</div>
              <DatePicker
                value={form.expected_close_date ?? undefined}
                onChange={(date) => setForm({ ...form, expected_close_date: (date as Date) ?? null })}
                style={{ width: '100%' }}
              />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>竞争对手</div>
            <Input value={form.competitor} onChange={(v) => setForm({ ...form, competitor: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>下一步动作</div>
            <Input value={form.next_action} onChange={(v) => setForm({ ...form, next_action: v })} />
          </div>
        </div>
      </Modal>
    </div>
  )
}
