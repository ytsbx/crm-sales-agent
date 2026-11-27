import { useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Modal, Pagination, Select, Tag, TextArea, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import EmptyState from '../../shared/components/EmptyState'
import FormLabel from '../../shared/components/FormLabel'
import { formatBeijingTime } from '../../shared/beijing-time'
import { listUsers } from '../../shared/api/system'
import type { WeComTransferPeopleSelection } from '../../shared/api/wecom'
import { getCustomer, listDuplicateCases, resolveDuplicateCase, type DuplicateCase } from '../../shared/api/customer'
import { usePermissions } from '../../shared/hooks/permissions'
import type { Customer } from '../../shared/types'
import './DuplicateCasePage.css'

const DECISIONS = {
  keep_both: {
    label: '判为不同客户，各自保留',
    description: '结案后，两条档案各自的跟进人、待办和单据保持不变。',
  },
  assign_existing: {
    label: '归并到同一组跟进人',
    description: '两条档案分开保留，双方原有跟进人合并成一组，都能看到两边档案。',
  },
  assign_new: {
    label: '指定一位同事共同跟进',
    description: '两条档案分开保留，并把你选定的同事加入两条档案的跟进人。',
  },
}
type Decision = keyof typeof DECISIONS

const DECISION_OPTIONS = Object.entries(DECISIONS).map(([value, { label }]) => ({ value, label }))
const SOURCE_LABELS: Record<string, string> = { import: '批量导入', create: '新建档案', manual: '手动检查' }
const EMPTY_FORM = { decision: undefined as Decision | undefined, owner: undefined as { value: number; label: string } | undefined, remark: '' }

function CaseCustomers({ record, customers }: { record: DuplicateCase; customers?: Customer[] }) {
  const pair = [
    { title: '本次核查的档案', id: record.customer_id, name: record.customer_name },
    { title: '疑似重复的已有档案', id: record.candidate_id, name: record.candidate_name },
  ]
  return (
    <div className="duplicate-case-pair">
      {pair.map((entry, index) => {
        const detail = customers?.[index]
        return (
          <div className="duplicate-case-customer" key={entry.id}>
            <span>{entry.title} · #{entry.id}</span>
            <Link to={`/customers/${entry.id}`}>{detail?.name ?? entry.name ?? `客户编号 ${entry.id}`}</Link>
            {detail && <span>{detail.region || '未填写地区'} · {(detail.followers ?? []).map((item) => item.name).join('、') || '暂无跟进人'}</span>}
          </div>
        )
      })}
    </div>
  )
}

function CaseEvidence({ record }: { record: DuplicateCase }) {
  return (
    <div className="duplicate-case-evidence">
      <strong>{record.score == null ? '查重依据' : `相似度 ${record.score} 分`}</strong>
      <span>{record.evidence?.reasons?.join('、') || '这条记录没有保存具体的匹配依据。'}</span>
    </div>
  )
}

export default function DuplicateCasePage() {
  const { can } = usePermissions()
  const canArbitrate = can('customer:assign')
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const transferOrigin = location.state as {
    fromWeComTransfer?: boolean
    selection?: WeComTransferPeopleSelection
  } | null
  const returnToTransfer = transferOrigin?.fromWeComTransfer && (
    <Button type="primary" theme="outline" onClick={() => navigate('/wecom', {
      state: { resumeWeComTransfer: transferOrigin.selection ?? {} },
    })}>返回离职交接</Button>
  )
  const customerId = Number(params.get('customer_id')) || undefined
  const status = params.get('status') === 'resolved' ? 'resolved' : 'pending'
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [record, setRecord] = useState<DuplicateCase | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [ownerKeyword, setOwnerKeyword] = useState('')
  const isReviewing = Boolean(record?.status === 'pending' && canArbitrate)

  const query = useQuery({
    queryKey: ['duplicate-cases', status, customerId, page, pageSize],
    queryFn: () => listDuplicateCases({ status, customer_id: customerId, page, page_size: pageSize }),
  })
  const customersQuery = useQuery({
    queryKey: ['duplicate-case-customers', record?.customer_id, record?.candidate_id],
    queryFn: () => Promise.all([getCustomer(record!.customer_id), getCustomer(record!.candidate_id)]),
    enabled: isReviewing,
    staleTime: 0,
  })
  const usersQuery = useQuery({
    queryKey: ['assignable-users', 'duplicate-case', ownerKeyword],
    queryFn: () => listUsers({ keyword: ownerKeyword, status: 'active', page: 1, page_size: 100 }),
    enabled: isReviewing && form.decision === 'assign_new',
  })
  const owners = (usersQuery.data?.items ?? []).map((user) => ({ value: user.id, label: `${user.name}（${user.username}）` }))
  if (form.owner && !owners.some((owner) => owner.value === form.owner?.value)) owners.unshift(form.owner)

  function showStatus(nextStatus: string) {
    const next = new URLSearchParams(params)
    if (nextStatus === 'resolved') next.set('status', 'resolved')
    else next.delete('status')
    setParams(next, { state: location.state })
    setPage(1)
  }

  function openRecord(item: DuplicateCase) {
    resolveMutation.reset()
    setRecord(item)
    setForm(EMPTY_FORM)
    setOwnerKeyword('')
  }

  const resolveMutation = useMutation({
    mutationFn: () => resolveDuplicateCase(record!.id, {
      decision: form.decision!,
      owner_id: form.decision === 'assign_new' ? form.owner?.value : undefined,
      remark: form.remark.trim() || null,
    }),
    onSuccess: (result) => {
      setRecord(result)
      showStatus('resolved')
      Toast.success('裁定已登记')
      for (const key of [
        ['duplicate-cases'], ['duplicate-case-customers'], ['customers'],
        ['customer', result.customer_id], ['customer', result.candidate_id], ['workbench'],
      ]) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: () => {
      void query.refetch()
      void customersQuery.refetch()
    },
  })
  const canSubmit = Boolean(isReviewing && customersQuery.isSuccess && form.decision
    && (form.decision !== 'assign_new' || form.owner))
  const rows = query.data?.items ?? []

  return (
    <div className="page-container duplicate-case-page">
      <PageHeader title="撞单裁定" subtitle="核对疑似重复的客户档案，决定两条档案分别或由同一位同事跟进。"
        extra={returnToTransfer || undefined} />

      <SectionCard style={{ flex: '0 0 auto' }} title={query.isSuccess ? `撞单记录（${query.data.total} 条）` : '撞单记录'} extra={
        <Select
          aria-label="裁定状态"
          value={status}
          onChange={(value) => showStatus(value as string)}
          optionList={[{ value: 'pending', label: '待裁定' }, { value: 'resolved', label: '已裁定' }]}
          style={{ width: 122 }}
        />
      }>
        {customerId && (
          <div className="duplicate-case-scope">
            <span>当前只看客户 <Link to={`/customers/${customerId}`}>#{customerId}</Link> 相关的记录</span>
            <Button size="small" theme="borderless" onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('customer_id')
              setParams(next, { state: location.state })
              setPage(1)
            }}>查看全部记录</Button>
          </div>
        )}
        {query.isError ? (
          <EmptyState title="暂时无法读取撞单记录" action={
            <Button size="small" onClick={() => void query.refetch()}>重新读取</Button>
          } />
        ) : query.isPending ? (
          <div role="status">正在读取撞单记录…</div>
        ) : rows.length === 0 ? (
          <EmptyState title={status === 'pending' ? '没有待裁定的撞单' : '没有已裁定的记录'}
            hint={status === 'pending'
              ? transferOrigin?.fromWeComTransfer
                ? '可返回离职交接重新看清单。如果客户仍冻结，请联系能查看两条档案的管理员。'
                : '新建、导入或手动检查时发现疑似重复的档案，会列在这里。'
              : undefined} />
        ) : (
          <ul className="duplicate-case-list">
            {rows.map((item) => (
              <li className="duplicate-case-row" key={item.id}>
                <CaseCustomers record={item} />
                <CaseEvidence record={item} />
                <div className="duplicate-case-action">
                  <div>
                    <Tag size="small" color={item.status === 'pending' ? 'orange' : 'green'}>{item.status === 'pending' ? '待裁定' : '已裁定'}</Tag>
                    <span>{SOURCE_LABELS[item.source] ?? item.source}</span>
                  </div>
                  <Button size="small" theme="outline" type="primary" onClick={() => openRecord(item)}>
                    {item.status === 'resolved' ? '查看裁定结果' : canArbitrate ? '核对并裁定' : '查看撞单依据'}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {((query.data?.total ?? 0) > pageSize || pageSize !== 20) && (
          <div className="duplicate-case-pagination">
            <Pagination currentPage={page} pageSize={pageSize} total={query.data?.total ?? 0} onPageChange={setPage}
              showSizeChanger pageSizeOpts={[10, 20, 50, 100]} onPageSizeChange={(value) => {
                setPageSize(value)
                setPage(1)
              }} />
          </div>
        )}
      </SectionCard>

      <Modal
        width="min(640px, calc(100vw - 24px))"
        className="workflow-scroll-modal"
        title={record?.status === 'resolved' ? '裁定结果' : isReviewing ? '核对客户档案' : '撞单依据'}
        visible={Boolean(record)}
        onCancel={() => { if (!resolveMutation.isPending) setRecord(null) }}
        footer={<div className="workflow-modal-actions">
          <Button disabled={resolveMutation.isPending} onClick={() => setRecord(null)}>{isReviewing ? '取消' : '关闭'}</Button>
          {record?.status === 'resolved' && returnToTransfer}
          {isReviewing && <Button theme="outline" type="primary" disabled={!canSubmit} loading={resolveMutation.isPending}
            onClick={() => resolveMutation.mutate()}>提交裁定</Button>}
        </div>}
      >
        {record && <div className="duplicate-case-modal">
          <CaseCustomers record={record} customers={isReviewing ? customersQuery.data : undefined} />
          <CaseEvidence record={record} />
          {record.status === 'resolved' ? (
            <div className="duplicate-case-summary">
              <strong>{record.decision_label ?? '已裁定'}</strong>
              {record.resolved_at && <span>{formatBeijingTime(record.resolved_at)}</span>}
              {record.resolved_owner_id && <span>裁定接手人 {form.owner?.value === record.resolved_owner_id
                ? form.owner.label : `员工编号 ${record.resolved_owner_id}`}</span>}
              <div>{record.remark || '没有登记备注。'}</div>
              {transferOrigin?.fromWeComTransfer && <span>返回离职交接，重新核对清单和接手人后再执行。</span>}
            </div>
          ) : isReviewing ? (
            <>
              {customersQuery.isPending && <div role="status">正在读取客户资料…</div>}
              {customersQuery.isError && <Banner type="warning" closeIcon={null} description={
                <span>暂时无法读取两条客户资料。<Button size="small" onClick={() => void customersQuery.refetch()}>重新读取资料</Button></span>
              } />}
              <div>
                <FormLabel required>裁定方式</FormLabel>
                <Select aria-label="裁定方式" value={form.decision} placeholder="请选择裁定方式"
                  optionList={DECISION_OPTIONS}
                  onChange={(value) => setForm({ ...form, decision: value as Decision })} style={{ width: '100%' }} />
                {form.decision && <p className="duplicate-case-hint">{DECISIONS[form.decision].description}</p>}
              </div>
              {form.decision === 'assign_new' && <div>
                <FormLabel required>共同跟进人</FormLabel>
                <Select aria-label="共同跟进人" remote filter value={form.owner?.value} placeholder="输入姓名或登录名查询在职同事"
                  loading={usersQuery.isFetching} optionList={owners} onSearch={(value) => setOwnerKeyword(value.trim())}
                  onChange={(value) => setForm({ ...form, owner: owners.find((owner) => owner.value === value) })}
                  style={{ width: '100%' }} />
                {usersQuery.isError && <p className="duplicate-case-hint">暂时无法读取员工。<Button size="small" onClick={() => void usersQuery.refetch()}>重新读取</Button></p>}
              </div>}
              <div>
                <FormLabel>裁定备注</FormLabel>
                <TextArea aria-label="裁定备注" placeholder="可记录核对结果，供之后查阅" value={form.remark}
                  rows={2} maxLength={255} onChange={(remark) => setForm({ ...form, remark })} />
              </div>
              {resolveMutation.isError && <Banner type="warning" closeIcon={null}
                description={resolveMutation.error.message} />}
            </>
          ) : (
            <div className="duplicate-case-hint">请联系有「分配客户」权限的同事核对并登记裁定。</div>
          )}
        </div>}
      </Modal>
    </div>
  )
}
