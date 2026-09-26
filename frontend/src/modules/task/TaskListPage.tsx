import { useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, DatePicker, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { completeTask, createTask, listTasks, postponeTask, type Task } from '../../shared/api/task'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'

const PRIORITY_COLOR: Record<string, TagTone> = { high: 'red', normal: 'blue', low: 'grey' }

export default function TaskListPage() {
  const queryClient = useQueryClient()
  const [mine, setMine] = useState(true)
  const [status, setStatus] = useState<string | undefined>('pending')
  const [overdue, setOverdue] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [createVisible, setCreateVisible] = useState(false)
  const [form, setForm] = useState({ title: '', priority: 'normal', due_at: null as Date | null })
  const [postponeTarget, setPostponeTarget] = useState<Task | null>(null)
  const [postponeDue, setPostponeDue] = useState<Date | null>(null)

  const query = useQuery({
    queryKey: ['tasks', { mine, status, overdue, page, pageSize }],
    queryFn: () => listTasks({ mine, status, overdue: overdue || undefined, page, page_size: pageSize }),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tasks'] })

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

  const completeMutation = useMutation({
    mutationFn: (id: number) => completeTask(id),
    onSuccess: () => {
      Toast.success('任务已完成')
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const postponeMutation = useMutation({
    mutationFn: () => postponeTask(postponeTarget!.id, postponeDue!.toISOString()),
    onSuccess: () => {
      Toast.success('任务已延期')
      setPostponeTarget(null)
      setPostponeDue(null)
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    { title: '任务', dataIndex: 'title' },
    {
      title: '关联',
      width: 200,
      render: (_: unknown, record: Task) =>
        record.opportunity_id ? (
          <Link to={`/opportunities/${record.opportunity_id}`} style={{ color: 'var(--crm-primary)' }}>
            商机 #{record.opportunity_id}
          </Link>
        ) : record.customer_id ? (
          <Link to={`/customers/${record.customer_id}`} style={{ color: 'var(--crm-primary)' }}>
            客户 #{record.customer_id}
          </Link>
        ) : (
          '-'
        ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 90,
      render: (value: string, record: Task) => (
        <Tag color={PRIORITY_COLOR[value] ?? 'grey'}>{record.priority_label}</Tag>
      ),
    },
    {
      title: '截止时间',
      dataIndex: 'due_at',
      width: 190,
      render: (value: string | null, record: Task) =>
        value ? (
          <span style={{ color: record.overdue ? 'var(--crm-error)' : undefined }}>
            {new Date(value).toLocaleString('zh-CN')}
            {record.overdue ? '（已逾期）' : ''}
          </span>
        ) : (
          '-'
        ),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string) => (value === '已完成' ? <Tag color="green">{value}</Tag> : value),
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, record: Task) =>
        record.status === 'done' || record.status === 'cancelled' ? (
          '-'
        ) : (
          <div style={{ display: 'flex', gap: 10 }}>
            <a style={{ color: 'var(--crm-primary)' }} onClick={() => completeMutation.mutate(record.id)}>
              完成
            </a>
            <a
              style={{ color: 'var(--crm-primary)' }}
              onClick={() => {
                setPostponeTarget(record)
                setPostponeDue(null)
              }}
            >
              延期
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

      <SectionCard>
        <div className="toolbar">
          <Checkbox
            checked={mine}
            onChange={(event) => {
              setMine(Boolean(event.target.checked))
              setPage(1)
            }}
          >
            只看我的
          </Checkbox>
          <Select
            placeholder="状态"
            value={status}
            onChange={(value) => {
              setStatus(value as string | undefined)
              setPage(1)
            }}
            optionList={[
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
              setOverdue(Boolean(event.target.checked))
              setPage(1)
            }}
          >
            只看逾期
          </Checkbox>
          <div style={{ flex: 1 }} />
          <Button theme="solid" onClick={() => setCreateVisible(true)}>
            新建任务
          </Button>
        </div>

        <Table<Task>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          empty="没有任务"
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
          <Input
            value={form.title}
            onChange={(v) => setForm({ ...form, title: v })}
            placeholder="例如：周五前回复客户报价"
          />
          <div style={{ display: 'flex', gap: 12 }}>
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
            <DatePicker
              type="dateTime"
              value={form.due_at ?? undefined}
              onChange={(date) => setForm({ ...form, due_at: (date as Date) ?? null })}
              placeholder="截止时间"
              style={{ flex: 1 }}
            />
          </div>
        </div>
      </Modal>

      <Modal
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
    </div>
  )
}
