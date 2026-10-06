/**
 * 撞单裁定（文档 §11.4 验收 20 / §11.5 :279）。
 *
 * 这一页的立场：**系统只摆证据，归属由人写**。
 * "谁先建档客户就归谁"不足以处理撞单——历史导入、重名公司、多人协作都会让
 * 建档时间失真，所以代码不提供"按时间自动判"的选项，只给人工结论。
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { listUsers } from '../../shared/api/system'
import {
  listDuplicateCases,
  resolveDuplicateCase,
  type DuplicateCase,
} from '../../shared/api/customer'
import { usePermissions } from '../../shared/hooks/permissions'
import { optionMatcher } from '../../shared/components/optionMatch'

// 与后端 DECISION_LABEL 同一套口径：没有"按建档时间自动判"这一项
const DECISION_OPTIONS = [
  { value: 'keep_both', label: '判为不同客户（各自保留）' },
  { value: 'assign_existing', label: '归已有客户的负责人' },
  { value: 'assign_new', label: '指定负责人' },
]

export default function DuplicateCasePage() {
  const { can } = usePermissions()
  const canArbitrate = can('customer:assign')
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('pending')
  // 真分页（返工单 6.5）：改筛选回第 1 页，否则翻到第 3 页再筛会看到空白
  const [page, setPage] = useState(1)
  const [form, setForm] = useState<{
    visible: boolean
    caseId?: number
    decision: string
    owner_id?: number
    remark: string
  }>({ visible: false, decision: 'keep_both', remark: '' })

  const query = useQuery({
    queryKey: ['duplicate-cases', status, page],
    queryFn: () => listDuplicateCases({ status, page, page_size: 20 }),
  })
  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: form.visible,
  })

  const resolveMutation = useMutation({
    mutationFn: () =>
      resolveDuplicateCase(form.caseId as number, {
        decision: form.decision,
        owner_id: form.decision === 'assign_new' ? (form.owner_id ?? null) : null,
        remark: form.remark || null,
      }),
    onSuccess: () => {
      Toast.success('裁定已登记')
      setForm({ visible: false, decision: 'keep_both', remark: '' })
      void queryClient.invalidateQueries({ queryKey: ['duplicate-cases'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div className="page-container">
      <PageHeader
        title="撞单裁定"
        subtitle="系统提示证据，归属由人裁定——不按建档先后自动判，也不误合并"
      />

      <SectionCard
        title={
          // 总数用后端给的 `total`：不能拿"本页条数"当总数 ——
          // 那会让用户以为"一共就这么些"，旧案件永远翻不到（返工单 6.5）
          `${status === 'pending' ? '待裁定' : '已裁定'}（${query.data?.total ?? 0} 条）`
        }
      >
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <Select
            value={status}
            onChange={(v) => {
              setStatus(v as string)
              setPage(1)
            }}
            optionList={[
              { value: 'pending', label: '待裁定' },
              { value: 'resolved', label: '已裁定' },
            ]}
            style={{ width: 140 }}
          />
        </div>

        <Table<DuplicateCase>
          rowKey="id"
          pagination={{
            currentPage: page,
            pageSize: 20,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
          }}
          loading={query.isLoading}
          dataSource={query.data?.items ?? []}
          empty="没有待裁定的撞单"
          columns={[
            {
              title: '新导入 / 新建',
              dataIndex: 'customer_name',
              width: 200,
              render: (v: string | null, r: DuplicateCase) => (
                <Link to={`/customers/${r.customer_id}`}>{v ?? `#${r.customer_id}`}</Link>
              ),
            },
            {
              title: '库里疑似同一条',
              dataIndex: 'candidate_name',
              width: 200,
              render: (v: string | null, r: DuplicateCase) => (
                <Link to={`/customers/${r.candidate_id}`}>{v ?? `#${r.candidate_id}`}</Link>
              ),
            },
            {
              title: '证据',
              render: (_: unknown, r: DuplicateCase) => {
                const reasons = r.evidence?.reasons ?? []
                return reasons.length ? (
                  <span style={{ fontSize: 12 }}>{reasons.join('、')}</span>
                ) : (
                  <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                )
              },
            },
            {
              title: '相似度',
              dataIndex: 'score',
              width: 90,
              render: (v: number | null) => (v == null ? '—' : `${v} 分`),
            },
            {
              title: '来源',
              dataIndex: 'source',
              width: 90,
              render: (v: string) =>
                ({
                  import: '批量导入',
                  create: '建档',
                  manual: '人工发起',
                })[v] ?? v,
            },
            {
              title: '结论',
              dataIndex: 'decision_label',
              width: 180,
              render: (v: string | null, r: DuplicateCase) =>
                r.status === 'pending' ? (
                  <Tag size="small" color="orange">
                    待裁定
                  </Tag>
                ) : (
                  (v ?? '—')
                ),
            },
            {
              title: '操作',
              width: 100,
              render: (_: unknown, r: DuplicateCase) =>
                r.status === 'pending' && canArbitrate ? (
                  <a
                    onClick={() =>
                      setForm({
                        visible: true,
                        caseId: r.id,
                        decision: 'keep_both',
                        remark: '',
                      })
                    }
                  >
                    裁定
                  </a>
                ) : (
                  <span style={{ color: 'var(--crm-text-3)' }}>—</span>
                ),
            },
          ]}
        />
      </SectionCard>

      <Modal
        title="裁定撞单"
        visible={form.visible}
        onCancel={() => setForm({ ...form, visible: false })}
        onOk={() => resolveMutation.mutate()}
        confirmLoading={resolveMutation.isPending}
        okText="提交裁定"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.7 }}>
            裁定后两条客户的归属会按结论调整，并写进归属变更历史。
            「判为不同客户」只结案、不动归属，也不会合并两条记录。
          </div>
          <Select
            value={form.decision}
            onChange={(v) => setForm({ ...form, decision: v as string })}
            optionList={DECISION_OPTIONS}
            style={{ width: '100%' }}
          />
          {form.decision === 'assign_new' && (
            <Select
              placeholder="指定负责人"
              filter={optionMatcher}
              value={form.owner_id}
              onChange={(v) => setForm({ ...form, owner_id: v as number })}
              optionList={(usersQuery.data?.items ?? []).map((u) => ({
                value: u.id,
                label: u.name,
              }))}
              style={{ width: '100%' }}
            />
          )}
          <Input
            placeholder="裁定依据（会留痕，建议写清楚）"
            value={form.remark}
            onChange={(v) => setForm({ ...form, remark: v })}
          />
          <Button
            theme="borderless"
            onClick={() => setStatus(status === 'pending' ? 'resolved' : 'pending')}
          >
            {status === 'pending' ? '看看已裁定的' : '回到待裁定'}
          </Button>
        </div>
      </Modal>
    </div>
  )
}
