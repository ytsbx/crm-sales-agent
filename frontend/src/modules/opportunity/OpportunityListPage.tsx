import { useEffect, useState } from 'react'
import { parseOptionalNumber } from '../../shared/number-input'
import { calendarDay } from '../../shared/calendar-day'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, TextArea, Toast } from '@douyinfe/semi-ui'

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
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
import StatusDot from '../../shared/components/StatusDot'
import { formatMoney, toTwoDecimals } from '../../shared/components/money'

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

  /* 1024 宽内容区约 700px，商机表六列合计 890px+，「成交日 / 风险 / 负责人」
     被裁到卡片外。窄屏切紧凑档：负责人并进客户第二行，金额/需求、
     成交日/风险各拆两行，列宽整体收紧。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string>('open')
  const [stageId, setStageId] = useState<number | undefined>()
  const [view, setView] = useState<'table' | 'board'>('table')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

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

  /* 「金额 / 需求」和「成交日 / 风险」这两列，演示库里几乎每条都是空的（只画一条淡灰横线）。
     460px 的宽度常年摆着一排横杠，把商机名和阶段挤窄。
     和文档页的到期日一样处理：本次结果里一个值都没有就不出这一列，
     有一条带值就照常显示 —— 不删信息，只删"一排空位"。（2026-11-19 巡检） */
  const rows = (query.data?.items ?? []) as Opportunity[]
  const hasAmountCol = rows.some((r) => Boolean(r.expected_amount) || Boolean(r.item_count))
  const hasCloseCol = rows.some((r) => Boolean(r.expected_close_date) || Boolean(r.risk_level))

  const columns = [
    {
      title: '商机名称',
      dataIndex: 'title',
      width: compact ? 150 : undefined,
      render: (text: string, record: Opportunity) => (
        <a
          title={text}
          style={{
            color: 'var(--crm-primary)',
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          onClick={() => navigate(`/opportunities/${record.id}`)}
        >
          {text}
        </a>
      ),
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      width: compact ? 160 : 220,
      render: (v: string | null, record: Opportunity) => (
        <div style={{ minWidth: 0 }}>
          <div
            title={v ?? '-'}
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {v ?? '-'}
          </div>
          {compact && (
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
              负责人 {record.owner_name ?? '-'}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '阶段',
      dataIndex: 'stage_name',
      width: compact ? 96 : 110,
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
    ...(hasAmountCol ? [{
      title: '金额 / 需求',
      width: compact ? 120 : 260,
      render: (_: unknown, record: Opportunity) => {
        const amount = record.expected_amount
          ? formatMoney(toTwoDecimals(record.expected_amount), 'CNY')
          : null
        const items = record.item_count ? `${record.item_count} 条需求` : null
        if (compact) {
          /* 窄屏拆两行：金额一行、需求条数一行，120px 才放得下完整金额 */
          if (!amount && !items) return <EmptyMark />
          return (
            <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
              {amount && <span>{amount}</span>}
              {items && <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{items}</span>}
            </div>
          )
        }
        const parts = [amount, items].filter(Boolean) as string[]
        return parts.length ? parts.join(' · ') : <EmptyMark />
      },
    }] : []),
    ...(hasCloseCol ? [{
      title: '成交日 / 风险',
      width: compact ? 116 : 200,
      render: (_: unknown, record: Opportunity) => {
        const date = record.expected_close_date ?? null
        const risk = record.risk_level ? (RISK_LABEL[record.risk_level] ?? record.risk_level) : null
        if (compact) {
          if (!date && !risk) return <EmptyMark />
          return (
            <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
              {date && <span>{date}</span>}
              {risk && <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>{risk}</span>}
            </div>
          )
        }
        const parts = [date, risk].filter(Boolean) as string[]
        return parts.length ? parts.join(' · ') : <EmptyMark />
      },
    }] : []),
    ...(compact
      ? []
      : [{ title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? <EmptyMark /> }]),
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
            <Button type="primary" theme="outline" onClick={() => setCreateVisible(true)}>
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
            className="opportunities-table"
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
        title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                <path d="M3 20h18" />
                <path d="M6 16l5-5 3.5 3.5L21 8" />
                <path d="M15 8h6v6" />
              </svg>
            </span>
            <div>
              新建商机
              <div className="modal-subtitle">客户和名称必填；金额、日期可以后补</div>
            </div>
          </div>
        }
        width={600}
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
        <div className="form-cards">
          <section className="form-card">
            <div className="form-card-title">这单是什么</div>
            <div className="form-card-grid">
              <div className="full">
                <div className="form-field-label">客户<span className="req">*</span></div>
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
              <div className="full">
                <div className="form-field-label">商机名称<span className="req">*</span></div>
                <Input
                  value={form.title}
                  onChange={(v) => setForm({ ...form, title: v })}
                  placeholder="例如：周转箱年度采购"
                />
              </div>
              <div>
                <div className="form-field-label">预计金额</div>
                <Input
                  value={form.expected_amount}
                  onChange={(v) => setForm({ ...form, expected_amount: v })}
                  placeholder="元，例如 120000"
                />
              </div>
              <div>
                <div className="form-field-label">预计成交日</div>
                <DatePicker
                  placeholder="不确定就空着"
                  value={form.expected_close_date ?? undefined}
                  onChange={(date) => setForm({ ...form, expected_close_date: (date as Date) ?? null })}
                  style={{ width: '100%' }}
                />
              </div>
            </div>
          </section>

          <section className="form-card">
            <div className="form-card-title">竞争与下一步</div>
            <div className="form-card-grid">
              <div className="full">
                <div className="form-field-label">竞争对手</div>
                <Input
                  value={form.competitor}
                  onChange={(v) => setForm({ ...form, competitor: v })}
                  placeholder="客户还在比的另一家；没有就空着"
                />
              </div>
              <div className="full">
                <div className="form-field-label">下一步</div>
                <TextArea
                  rows={2}
                  value={form.next_action}
                  onChange={(v) => setForm({ ...form, next_action: v })}
                  placeholder="例如：下周带样箱去客户仓库试装"
                />
              </div>
            </div>
          </section>
        </div>
      </Modal>
    </div>
  )
}
