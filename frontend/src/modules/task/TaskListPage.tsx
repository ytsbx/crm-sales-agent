import { formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyNode } from '../../shared/components/EmptyState'
import { Button, Checkbox, DatePicker, Input, Modal, Popconfirm, Select, Table, Toast } from '@douyinfe/semi-ui'

import {
  batchCompleteTasks,
  completeTask,
  createTask,
  listTasks,
  postponeTask,
  transferTask,
  type Task,
} from '../../shared/api/task'
import { listUsers } from '../../shared/api/system'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import StatusDot from '../../shared/components/StatusDot'

const PRIORITY_COLOR: Record<string, TagTone> = { high: 'red', normal: 'blue', low: 'grey' }

/**
 * 任务的「关联」列显示哪一个：按「**最具体的那个**」排。
 *
 * 打样 > 报价 > 订单 > 合同 > 商机 > 客户 —— 越靠前越接近用户真正要打开的那份原件。
 * 报价 / 订单走任务表自己的列（后端连编号一起给了）；打样 / 合同走 `source_business_*`
 * （任务表没有它们的列）。跳转直接落到**详情**而不是列表页 —— 让人自己再找一遍
 * 等于没做（第十一批 11.6 复审）。
 */
function primaryTaskLink(record: Task): { label: string; to: string } | null {
  if (record.source_business_type === 'sample' && record.source_business_id) {
    // 打样单没有独立编号字段，用 id（打样列表里也是显示「打样申请 #id」）
    return {
      label: `打样单编号 ${record.source_business_id}`,
      to: `/samples/${record.source_business_id}`,
    }
  }
  if (record.quote_id) {
    return {
      // 报价一定有单号（`quote_no` 非空），所以**没有**退回内部编号的分支
      label: `报价 ${record.quote_no}`,
      to: `/quotes/${record.quote_id}`,
    }
  }
  if (record.order_id) {
    return {
      label: `订单 ${record.order_no}`,
      to: `/orders/${record.order_id}`,
    }
  }
  if (record.source_business_type === 'contract' && record.source_business_id) {
    return {
      label: `${record.task_type === 'contract_signed_upload' ? '合同' : '月结协议'} ${record.source_doc_no || ''}`,
      to: `/documents?contract=${record.source_business_id}`,
    }
  }
  if (record.opportunity_id) {
    return { label: `商机编号 ${record.opportunity_id}`, to: `/opportunities/${record.opportunity_id}` }
  }
  if (record.customer_id && record.customer_name) {
    return { label: record.customer_name, to: `/customers/${record.customer_id}` }
  }
  return null
}

export default function TaskListPage() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const mine = searchParams.get('mine') !== 'false'
  const status = searchParams.has('status') ? searchParams.get('status') || undefined : 'pending'
  const overdue = searchParams.get('overdue') === 'true'
  const setFilter = (name: string, value: string) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      next.set(name, value)
      if (name !== 'page') next.set('page', '1')
      return next
    }, { replace: true })
  }
  const requestedPage = Number(searchParams.get('page') ?? 1)
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1
  const setPage = (value: number) => setFilter('page', String(value))
  const [pageSize, setPageSize] = useState(20)

  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState({ title: '', priority: 'normal', due_at: null as Date | null })
  const [postponeTarget, setPostponeTarget] = useState<Task | null>(null)
  const [postponeDue, setPostponeDue] = useState<Date | null>(null)
  // 转交（审查 B2-06）：接口与 api 函数一直有，页面此前没有入口
  const [transferTarget, setTransferTarget] = useState<Task | null>(null)
  const [transferOwner, setTransferOwner] = useState<number | null>(null)
  // 批量完成（审查 B2-06）：勾选 + 批量操作入口
  const [selectedIds, setSelectedIds] = useState<number[]>([])

  const query = useQuery({
    queryKey: ['tasks', { mine, status, overdue, page, pageSize }],
    queryFn: () => listTasks({ mine, status, overdue: overdue || undefined, page, page_size: pageSize }),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tasks'] })

  // 转交目标只列**在职**人员：停用的人接不了任务（后端也会拦）
  const usersQuery = useQuery({
    queryKey: ['users', { status: 'active' }],
    queryFn: () => listUsers({ status: 'active', page_size: 200 }),
    enabled: transferTarget !== null,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      createTask({
        title: form.title,
        due_at: form.due_at ? form.due_at.toISOString() : null,
        priority: form.priority,
      }),
    onSuccess: () => {
      Toast.success('任务已创建')
      setCreateVisible(false)
      setForm({ title: '', priority: 'normal', due_at: null })
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const transferMutation = useMutation({
    // 带上"用户看到的那一版"：并发时后端能判断出你看到的单子已经过期（审查 B2-03）
    mutationFn: () => transferTask(transferTarget!.id, transferOwner!, transferTarget!.updated_at),
    onSuccess: (task) => {
      Toast.success(`已转交给「${task.owner_name ?? '新负责人'}」`)
      setTransferTarget(null)
      setTransferOwner(null)
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const batchCompleteMutation = useMutation({
    mutationFn: () => batchCompleteTasks(selectedIds),
    onSuccess: (result) => {
      // 成功 / 跳过 / 原因都要说清，否则操作的人不知道哪几条没成（审查 B2-06 的要求）
      const done = result.completed?.length ?? 0
      const skipped = result.skipped ?? []
      if (skipped.length === 0) {
        Toast.success(`已完成 ${done} 条`)
      } else {
        const detail = skipped
          .map((row) => `编号 ${row.task_id} ${row.reason}`)
          .slice(0, 3)
          .join('；')
        Toast.warning(
          `已完成 ${done} 条，跳过 ${skipped.length} 条：${detail}${skipped.length > 3 ? ' …' : ''}`,
        )
      }
      setSelectedIds([])
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const completeMutation = useMutation({
    // 版本取**列表里这一行**的 `updated_at` —— 那正是用户点按钮时屏幕上那一版
    mutationFn: (id: number) => {
      const row = (query.data?.items ?? []).find((t) => t.id === id)
      return completeTask(id, undefined, row?.updated_at)
    },
    onSuccess: () => {
      Toast.success('任务已完成')
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const postponeMutation = useMutation({
    mutationFn: () =>
      postponeTask(postponeTarget!.id, postponeDue!.toISOString(), postponeTarget!.updated_at),
    onSuccess: () => {
      Toast.success('任务已延期')
      setPostponeTarget(null)
      setPostponeDue(null)
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /* 1024 宽内容区约 700px，任务表七列合计 909px+，「截止时间」右半截
     「状态 / 操作」整列被裁，底部还挂横向滚动条。窄屏切紧凑档。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const columns = [
    {
      title: '',
      width: 44,
      // 终态任务不能再批量完成，勾选框直接禁用（后端也会跳过并给原因）
      render: (_: unknown, record: Task) => (
        <Checkbox
          checked={selectedIds.includes(record.id)}
          disabled={record.status === 'done' || record.status === 'cancelled' || record.task_type === 'contract_signed_upload'}
          title={record.task_type === 'contract_signed_upload' ? '上传回签件归档后自动完成' : undefined}
          onChange={(e) => {
            const on = (e.target as HTMLInputElement).checked
            setSelectedIds((prev) =>
              on ? [...prev, record.id] : prev.filter((id) => id !== record.id),
            )
          }}
        />
      ),
    },
    {
      title: '任务',
      dataIndex: 'title',
      width: compact ? 152 : 206,
      render: (text: string) => (
        <span
          title={text}
          style={{
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {text}
        </span>
      ),
    },
    {
      title: '关联',
      width: compact ? 108 : 182,
      render: (_: unknown, record: Task) => {
        // 一张任务可能同时挂着客户、商机、报价、订单、打样、合同。这一列只显示
        // **最具体的那一个**并直接跳过去；全铺出来会挤成六行，而用户要找的通常
        // 就是那份原件（第十一批 11.6 复审：此前只认合同 + 商机 + 客户，
        // 报价/订单/打样一律退化成「客户 #3」，看不出业务来源是哪一份）。
        const link = primaryTaskLink(record)
        if (!link) return record.customer_id ? '客户不可查看' : '-'
        return (
          <Link to={link.to} title={link.label} style={{ color: 'var(--crm-primary)' }}>
            {link.label}
          </Link>
        )
      },
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: compact ? 76 : 90,
      render: (value: string, record: Task) => (
        <StatusDot label={record.priority_label} tone={(PRIORITY_COLOR[value] ?? 'grey') as TagTone} />
      ),
    },
    {
      title: '截止时间',
      dataIndex: 'due_at',
      /* 190 放不下「2026/11/2 09:28 + 逾期标签」，日期被折成了两行；
         日期和标签必须待在同一行，否则整表的行高参差不齐。 */
      width: compact ? 94 : 202,
      // 逾期别拼成括号跟在日期后面（「2026/11/2 09:28:47（已逾期）」又长又糊）：
      // 日期一个色、状态一个小标签，扫一眼就分得出来
      render: (value: string | null, record: Task) =>
        value ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            {/* 精确到分钟就够：任务截止时间带秒既没用、又把这一列撑得很长 */}
            <span style={{ color: record.overdue ? 'var(--crm-error)' : undefined }}>
              {/* 窄屏省掉年份：94px 只放得下「11-20 15:21」，逾期标签另起一行 */}
              {compact ? formatBeijingTime(value).slice(5, 16) : formatBeijingTime(value).slice(0, 16)}
            </span>
            {record.overdue && (
              <StatusDot label="逾期" tone="red" bold />
            )}
          </span>
        ) : (
          '-'
        ),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: compact ? 92 : 100,
      // 状态一列也统一成圆点：只有「已完成」上绿点，其余保持灰点，
      // 和「优先级」列的写法对齐，整行不会一半是色块一半是裸文字。
      render: (value: string) => (
        <StatusDot label={value} tone={value === '已完成' ? 'green' : value === '逾期' ? 'red' : 'grey'} />
      ),
    },
    ...(compact
      ? []
      : [{ title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' }]),
    {
      title: '操作',
      width: compact ? 132 : 192,
      render: (_: unknown, record: Task) =>
        record.status === 'done' || record.status === 'cancelled' ? (
          '-'
        ) : record.task_type === 'contract_signed_upload' ? (
          // 文案只留动作，说明放进 title：宽屏那 12 个字的括号注释会把操作列撑出表格
          // （2026-11-18 截图：1440 下这张表出现横向滚动条，右边「去登记回签」被切）
          <Link to={`/documents?contract=${record.source_business_id}`} title="归档后自动完成">
              {'去登记回签'}
            </Link>
        ) : (
          <div
            style={
              compact
                ? { display: 'flex', gap: 12, whiteSpace: 'nowrap', fontSize: 13 }
                : { display: 'flex', gap: 10 }
            }
          >
            <Popconfirm
              title="确认完成这条任务？"
              content="完成后任务会从未完成列表中移除。"
              onConfirm={() => completeMutation.mutate(record.id)}
            >
              <a style={{ color: 'var(--crm-primary)' }}>完成</a>
            </Popconfirm>
            <a
              style={{ color: 'var(--crm-primary)' }}
              onClick={() => {
                setPostponeTarget(record)
                setPostponeDue(null)
              }}
            >
              延期
            </a>
            {/* 转交（审查 B2-06）：接口与 api 函数一直有，此前没有页面入口 */}
            <a
              style={{ color: 'var(--crm-primary)' }}
              onClick={() => {
                setTransferTarget(record)
                setTransferOwner(null)
              }}
            >
              转交
            </a>
          </div>
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="销售任务"
        subtitle="任务记录的是「接下来要做什么」，跟进记录的是「已经发生了什么」"
      />

      {/* 只有几行任务时，卡片不该被拉满整屏（详见 .card-block.auto-height） */}
      <SectionCard className={(query.data?.items?.length ?? 0) <= 8 ? 'auto-height' : undefined}>
        <div className="toolbar">
          <Checkbox
            checked={mine}
            onChange={(event) => {
              setFilter('mine', String(Boolean(event.target.checked)))
            }}
          >
            只看我的
          </Checkbox>
          <Select
            placeholder="状态"
            value={status}
            onChange={(value) => {
              setFilter('status', (value as string | undefined) ?? '')
            }}
            optionList={[
              { value: 'active', label: '未完成' },
              { value: 'pending', label: '待处理' },
              { value: 'doing', label: '处理中' },
              { value: 'done', label: '已完成' },
              { value: 'cancelled', label: '已取消' },
            ]}
            style={{ width: 130 }}
            showClear
          />
          <Checkbox
            checked={overdue}
            onChange={(event) => {
              setFilter('overdue', String(Boolean(event.target.checked)))
            }}
          >
            只看逾期
          </Checkbox>
          <div style={{ flex: 1 }} />
          {/* 批量完成（审查 B2-06）：勾选后一次性完成，成功/跳过与原因都要说清 */}
          <Popconfirm
            title={`确认完成选中的 ${selectedIds.length} 条任务？`}
            onConfirm={() => batchCompleteMutation.mutate()}
            disabled={selectedIds.length === 0 || batchCompleteMutation.isPending}
          >
            <Button
              disabled={selectedIds.length === 0 || batchCompleteMutation.isPending}
              loading={batchCompleteMutation.isPending}
            >
              批量完成{selectedIds.length ? `（已选 ${selectedIds.length}）` : ''}
            </Button>
          </Popconfirm>
          <Button type="primary" theme="outline" onClick={() => setCreateVisible(true)}>
            新建任务
          </Button>
        </div>

        <Table<Task>
          className="tasks-table"
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          empty={emptyNode(query, {
            title: '没有任务',
            hint: '点右上角「新建任务」，把「接下来要做什么」记下来 —— 任务记将来，跟进记过去。',
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
      </SectionCard>

      <Modal
        width={520}
        title="新建任务"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!form.title.trim()) {
            Toast.warning('任务标题必填')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>任务标题</FormLabel>
            <Input
              value={form.title}
              onChange={(v) => setForm({ ...form, title: v })}
              placeholder="例如：周五前回复客户报价"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div>
              {/* 「优先级」这句说明必须如实：查过全库，priority **不参与任何**
                  排序、筛选、提醒或统计（任务列表是按「状态 → 截止时间」排的，
                  见 backend/app/modules/task/router.py 的 order_by）。
                  它唯一的去处是列表里那个彩色标签。
                  不写这句，用户会以为选「高」就会被优先处理——那是个空承诺。
                  ⚠️ 商机详情页有一个一模一样的弹窗，改文案时两处同步。 */}
              <FormLabel hint="仅作轻重标记，不影响排序和提醒">优先级</FormLabel>
              <Select
                value={form.priority}
                onChange={(value) => setForm({ ...form, priority: value as string })}
                optionList={[
                  { value: 'high', label: '高' },
                  { value: 'normal', label: '中' },
                  { value: 'low', label: '低' },
                ]}
                style={{ width: 140 }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel>截止时间</FormLabel>
              <DatePicker
                type="dateTime"
                value={form.due_at ?? undefined}
                onChange={(date) => setForm({ ...form, due_at: (date as Date) ?? null })}
                placeholder="选择时间"
                style={{ width: '100%' }}
              />
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        width={520}
        title={`延期任务：${postponeTarget?.title ?? ''}`}
        visible={Boolean(postponeTarget)}
        onCancel={() => setPostponeTarget(null)}
        onOk={() => {
          if (!postponeDue) {
            Toast.warning('请选择新的截止时间')
            return
          }
          postponeMutation.mutate()
        }}
        confirmLoading={postponeMutation.isPending}
        okText="确认延期"
      >
        <DatePicker
          type="dateTime"
          value={postponeDue ?? undefined}
          onChange={(date) => setPostponeDue((date as Date) ?? null)}
          style={{ width: '100%' }}
        />
      </Modal>

      {/* 转交（审查 B2-06）：目标只列在职人员，停用的后端也会拦 */}
      <Modal
        width={520}
        title={`转交任务：${transferTarget?.title ?? ''}`}
        visible={Boolean(transferTarget)}
        onCancel={() => {
          setTransferTarget(null)
          setTransferOwner(null)
        }}
        onOk={() => {
          if (!transferOwner) {
            Toast.warning('请选择转交给谁')
            return
          }
          transferMutation.mutate()
        }}
        confirmLoading={transferMutation.isPending}
        okText="确认转交"
      >
        <FormLabel>转交给</FormLabel>
        <Select
          placeholder="选择在职同事"
          value={transferOwner ?? undefined}
          onChange={(value) => setTransferOwner((value as number) ?? null)}
          loading={usersQuery.isLoading}
          style={{ width: '100%' }}
          filter
        >
          {(usersQuery.data?.items ?? []).map((user) => (
            <Select.Option key={user.id} value={user.id} label={user.name}>
              {user.name}
              {user.department ? `（${user.department}）` : ''}
            </Select.Option>
          ))}
        </Select>
        <div style={{ marginTop: 8, color: 'var(--crm-text-secondary)', fontSize: 12 }}>
          转交只改「谁来做」；这条任务原来是谁的、什么时候建的，审计里都留着。
        </div>
      </Modal>
    </div>
  )
}
