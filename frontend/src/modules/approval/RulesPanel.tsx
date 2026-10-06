import { useState, type ComponentProps } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Table,
  Tag,
  Toast,
} from '@douyinfe/semi-ui'

import {
  createApprovalRule,
  deleteApprovalRule,
  listApprovalRuleVersions,
  listApprovalRules,
  listConditionFields,
  publishApprovalRule,
  runApprovalSandbox,
  toggleApprovalRule,
  updateApprovalRule,
  RULE_KIND_LABEL,
  type ApprovalRuleRow,
  type ConditionFieldMeta,
  type RuleCondition,
  type SandboxResult,
} from '../../shared/api/approval'
import { listQuotes, listQuoteVersions } from '../../shared/api/quote'
import SectionCard from '../../shared/components/SectionCard'
import { optionMatcher } from '../../shared/components/optionMatch'

type TagColor = ComponentProps<typeof Tag>['color']

const KIND_TONE: Record<string, TagColor> = {
  auto_pass: 'green',
  express: 'blue',
  exception_route: 'red',
}

const KIND_HINT: Record<string, string> = {
  auto_pass: '条件全部命中 → 提交即通过，不进审批流（留痕可查）',
  express: '条件全部命中 → 跳过金额分档的高层级，由第一级（主管）直接审批',
  exception_route: '条件全部命中 → 正常分档审批后，追加一个会签节点（一票否决）',
}

interface DraftState {
  id?: number
  name: string
  kind: string
  priority: number
  description: string
  conditions: RuleCondition[]
  coSignRoles: string
  coSignLabel: string
}

const emptyDraft = (): DraftState => ({
  name: '',
  kind: 'auto_pass',
  priority: 100,
  description: '',
  conditions: [{ field: 'gross_margin', op: 'gte', value: 25 }],
  coSignRoles: 'finance',
  coSignLabel: '财务会签',
})

export default function RulesPanel() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<DraftState | null>(null)
  const [versionTarget, setVersionTarget] = useState<ApprovalRuleRow | null>(null)

  const rulesQuery = useQuery({ queryKey: ['approval-rules'], queryFn: listApprovalRules })
  const fieldsQuery = useQuery({ queryKey: ['approval-rule-fields'], queryFn: listConditionFields })
  const fields = fieldsQuery.data ?? []
  const fieldMeta = (field: string) => fields.find((f) => f.field === field)

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['approval-rules'] })

  const saveMutation = useMutation({
    mutationFn: (draft: DraftState) => {
      const payload = {
        name: draft.name,
        kind: draft.kind,
        priority: draft.priority,
        description: draft.description || null,
        conditions: draft.conditions,
        action:
          draft.kind === 'exception_route'
            ? {
                add_node_role_codes: draft.coSignRoles
                  .split(/[,，\s]+/)
                  .filter(Boolean),
                add_node_label: draft.coSignLabel || '财务会签',
                veto: true,
              }
            : {},
      }
      return draft.id ? updateApprovalRule(draft.id, payload) : createApprovalRule(payload)
    },
    onSuccess: () => {
      Toast.success('已保存（草稿）')
      setEditing(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      toggleApprovalRule(id, enabled),
    onSuccess: () => refresh(),
    onError: (error: Error) => {
      Toast.error(error.message)
      refresh()
    },
  })

  const publishMutation = useMutation({
    mutationFn: (id: number) => publishApprovalRule(id),
    onSuccess: (data) => {
      Toast.success(`已发布 V${data.published_version_no}，对新提交的审批生效`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteApprovalRule(id),
    onSuccess: () => {
      Toast.success('已删除')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    {
      title: '规则',
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{row.name}</div>
          {row.description && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 2 }}>{row.description}</div>
          )}
        </div>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (kind: string) => (
        <Tag color={(KIND_TONE[kind] ?? 'grey') as TagColor}>{RULE_KIND_LABEL[kind] ?? kind}</Tag>
      ),
    },
    {
      title: '触发条件',
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div style={{ fontSize: 12, color: 'var(--crm-text-2)', display: 'grid', gap: 2 }}>
          {(row.conditions ?? []).map((cond, index) => {
            const meta = fieldMeta(cond.field)
            return (
              <span key={index}>
                {meta?.label ?? cond.field}{' '}
                {row.conditions_pretty?.[index]?.value_label ??
                  `${cond.op} ${String(cond.value)}`}
              </span>
            )
          })}
        </div>
      ),
    },
    { title: '优先级', dataIndex: 'priority', width: 80 },
    {
      title: '生效状态',
      width: 170,
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Switch
            checked={row.enabled}
            size="small"
            onChange={(checked) => toggleMutation.mutate({ id: row.id, enabled: checked })}
          />
          {row.published_version_no === 0 ? (
            <Tag color="grey" size="small">未发布</Tag>
          ) : (
            <Tag color="cyan" size="small">V{row.published_version_no}</Tag>
          )}
          {row.has_draft_changes && row.published_version_no > 0 && (
            <Tag color="orange" size="small">草稿待发布</Tag>
          )}
        </div>
      ),
    },
    {
      title: '操作',
      width: 190,
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div style={{ display: 'flex', gap: 10, fontSize: 13 }}>
          <a style={{ color: 'var(--crm-primary)' }} onClick={() => toEdit(row)}>
            编辑
          </a>
          <a style={{ color: 'var(--crm-success)' }} onClick={() => publishMutation.mutate(row.id)}>
            发布
          </a>
          <a style={{ color: 'var(--crm-text-2)' }} onClick={() => setVersionTarget(row)}>
            版本
          </a>
          <Popconfirm title="删除这条规则？" onConfirm={() => deleteMutation.mutate(row.id)}>
            <a style={{ color: 'var(--crm-error)' }}>删除</a>
          </Popconfirm>
        </div>
      ),
    },
  ]

  const toEdit = (row: ApprovalRuleRow) => {
    setEditing({
      id: row.id,
      name: row.name,
      kind: row.kind,
      priority: row.priority,
      description: row.description ?? '',
      conditions: (row.conditions ?? []).map((c) => ({ ...c })),
      coSignRoles: String((row.action?.add_node_role_codes as string[]) ?? ['finance']).replace(/[\[\]'"]/g, ''),
      coSignLabel: String(row.action?.add_node_label ?? '财务会签'),
    })
  }

  return (
    <>
      <SectionCard>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
            报价提交审批时按优先级逐条求值，第一条命中的规则决定走向；
            草稿发布后才生效，启停立即生效
          </div>
          <Button theme="solid" onClick={() => setEditing(emptyDraft())}>
            新建规则
          </Button>
        </div>
        <Table<ApprovalRuleRow>
          columns={columns}
          dataSource={rulesQuery.data ?? []}
          loading={rulesQuery.isLoading}
          rowKey="id"
          pagination={false}
          empty="还没有规则"
        />
      </SectionCard>

      <SandboxPanel fields={fields} />

      <Modal
        title={editing?.id ? '编辑规则（草稿，发布后生效）' : '新建规则'}
        visible={Boolean(editing)}
        onCancel={() => setEditing(null)}
        onOk={() => {
          if (!editing) return
          if (!editing.name.trim()) {
            Toast.warning('请填写规则名称')
            return
          }
          saveMutation.mutate(editing)
        }}
        confirmLoading={saveMutation.isPending}
        width={640}
        okText="保存草稿"
      >
        {editing && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={labelStyle}>规则名称</div>
                <Input
                  value={editing.name}
                  onChange={(v) => setEditing({ ...editing, name: v })}
                  placeholder="例如：高毛利小额自动免审"
                />
              </div>
              <div style={{ width: 130 }}>
                <div style={labelStyle}>优先级（小者先）</div>
                <InputNumber
                  value={editing.priority}
                  onChange={(v) => setEditing({ ...editing, priority: Number(v) || 100 })}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <div>
              <div style={labelStyle}>规则类型</div>
              <Select
                value={editing.kind}
                onChange={(v) => setEditing({ ...editing, kind: v as string })}
                style={{ width: '100%' }}
                optionList={Object.entries(RULE_KIND_LABEL).map(([value, label]) => ({ value, label }))}
              />
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                {KIND_HINT[editing.kind]}
              </div>
            </div>

            <div>
              <div style={labelStyle}>触发条件（全部命中才触发）</div>
              <div style={{ display: 'grid', gap: 8 }}>
                {editing.conditions.map((cond, index) => {
                  const meta = fieldMeta(cond.field)
                  return (
                    <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Select
                        value={cond.field}
                        style={{ width: 190 }}
                        onChange={(v) => {
                          const next = fieldMeta(v as string)
                          const conditions = [...editing.conditions]
                          conditions[index] = {
                            field: v as string,
                            op: next?.ops[0] ?? 'gte',
                            value: next?.value_type === 'bool' ? false : '',
                          }
                          setEditing({ ...editing, conditions })
                        }}
                        optionList={fields.map((f) => ({ value: f.field, label: f.label }))}
                      />
                      <Select
                        value={cond.op}
                        style={{ width: 90 }}
                        onChange={(v) => {
                          const conditions = [...editing.conditions]
                          conditions[index] = { ...cond, op: v as string }
                          setEditing({ ...editing, conditions })
                        }}
                        optionList={(meta?.ops ?? ['gte']).map((op) => ({
                          value: op,
                          label: op === 'gte' ? '≥' : op === 'lte' ? '≤' : op === 'eq' ? '=' : '属于',
                        }))}
                      />
                      {meta?.value_type === 'bool' ? (
                        <Select
                          value={String(cond.value)}
                          style={{ width: 120 }}
                          onChange={(v) => {
                            const conditions = [...editing.conditions]
                            conditions[index] = { ...cond, value: v === 'true' }
                            setEditing({ ...editing, conditions })
                          }}
                          optionList={[
                            { value: 'false', label: '否' },
                            { value: 'true', label: '是' },
                          ]}
                        />
                      ) : meta?.value_type === 'number' ? (
                        <InputNumber
                          value={Number(cond.value) || undefined}
                          onChange={(v) => {
                            const conditions = [...editing.conditions]
                            conditions[index] = { ...cond, value: v ?? 0 }
                            setEditing({ ...editing, conditions })
                          }}
                          suffix={meta.unit}
                          style={{ flex: 1 }}
                        />
                      ) : (
                        <Input
                          value={Array.isArray(cond.value) ? (cond.value as string[]).join(',') : String(cond.value ?? '')}
                          placeholder={cond.op === 'in' ? '多个值用英文逗号分隔，如 A,B' : '值'}
                          onChange={(v) => {
                            const conditions = [...editing.conditions]
                            conditions[index] = {
                              ...cond,
                              value: cond.op === 'in' ? v.split(/[,，]/).map((s) => s.trim()).filter(Boolean) : v,
                            }
                            setEditing({ ...editing, conditions })
                          }}
                          style={{ flex: 1 }}
                        />
                      )}
                      <a
                        style={{ color: 'var(--crm-error)', flexShrink: 0 }}
                        onClick={() =>
                          setEditing({
                            ...editing,
                            conditions: editing.conditions.filter((_, i) => i !== index),
                          })
                        }
                      >
                        删除
                      </a>
                    </div>
                  )
                })}
              </div>
              <div style={{ marginTop: 8 }}>
                <a style={{ color: 'var(--crm-primary)', fontSize: 13 }} onClick={() => setEditing({
                  ...editing,
                  conditions: [...editing.conditions, { field: fields[0]?.field ?? 'gross_margin', op: 'gte', value: 0 }],
                })}>
                  + 加一个条件
                </a>
              </div>
              {editing.conditions.some((c) => fieldMeta(c.field)) && (
                <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  {editing.conditions
                    .map((c) => fieldMeta(c.field))
                    .filter(Boolean)
                    .map((meta) => `${meta!.label}：${meta!.hint}`)
                    .join('；')}
                </div>
              )}
            </div>

            {editing.kind === 'exception_route' && (
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={labelStyle}>会签角色（角色编码，逗号分隔）</div>
                  <Input
                    value={editing.coSignRoles}
                    onChange={(v) => setEditing({ ...editing, coSignRoles: v })}
                    placeholder="finance"
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={labelStyle}>会签节点名称</div>
                  <Input
                    value={editing.coSignLabel}
                    onChange={(v) => setEditing({ ...editing, coSignLabel: v })}
                    placeholder="财务会签"
                  />
                </div>
              </div>
            )}

            <div>
              <div style={labelStyle}>备注</div>
              <Input
                value={editing.description}
                onChange={(v) => setEditing({ ...editing, description: v })}
                placeholder="给管理员看的说明（可选）"
              />
            </div>
          </div>
        )}
      </Modal>

      <VersionDrawer rule={versionTarget} onClose={() => setVersionTarget(null)} />
    </>
  )
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--crm-text-3)',
  marginBottom: 4,
}

// ---------------------------------------------------------------- 版本历史

function VersionDrawer({ rule, onClose }: { rule: ApprovalRuleRow | null; onClose: () => void }) {
  const versionsQuery = useQuery({
    queryKey: ['approval-rule-versions', rule?.id],
    queryFn: () => listApprovalRuleVersions(rule!.id),
    enabled: Boolean(rule),
  })
  return (
    <Modal title={rule ? `版本历史：${rule.name}` : ''} visible={Boolean(rule)} onCancel={onClose} footer={null} width={620}>
      <div style={{ display: 'grid', gap: 10, maxHeight: 420, overflow: 'auto' }}>
        {(versionsQuery.data ?? []).map((version) => (
          <SectionCard key={version.id}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>V{version.version_no}</span>
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {new Date(version.published_at).toLocaleString('zh-CN')}
                {version.is_current && <Tag color="cyan" size="small" style={{ marginLeft: 8 }}>当前生效</Tag>}
              </span>
            </div>
            <pre style={preStyle}>
              {JSON.stringify(
                { 优先级: version.payload.priority, 条件: version.payload.conditions, 动作: version.payload.action },
                null,
                2,
              )}
            </pre>
          </SectionCard>
        ))}
        {(versionsQuery.data ?? []).length === 0 && (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>还没有发布过版本</div>
        )}
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------- 规则沙盒

function SandboxPanel({ fields: _fields }: { fields: ConditionFieldMeta[] }) {
  const [keyword, setKeyword] = useState('')
  const [quoteId, setQuoteId] = useState<number | null>(null)
  const [versionId, setVersionId] = useState<number | null>(null)
  const [result, setResult] = useState<SandboxResult | null>(null)

  const quotesQuery = useQuery({
    queryKey: ['sandbox-quotes', keyword],
    queryFn: () => listQuotes({ keyword: keyword || undefined, page_size: 20 }),
  })
  const versionsQuery = useQuery({
    queryKey: ['sandbox-versions', quoteId],
    queryFn: () => listQuoteVersions(quoteId!),
    enabled: Boolean(quoteId),
  })

  const sandboxMutation = useMutation({
    mutationFn: () => runApprovalSandbox(versionId!),
    onSuccess: (data) => setResult(data),
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <SectionCard style={{ marginTop: 16 }}>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>规则沙盒（发布前先试算，不动任何数据）</div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Input
          value={keyword}
          onChange={setKeyword}
          placeholder="搜报价单号/客户"
          style={{ width: 200 }}
          showClear
        />
        <Select
          value={quoteId}
          onChange={(v) => {
            setQuoteId(v as number)
            setVersionId(null)
            setResult(null)
          }}
          placeholder="选报价单"
          style={{ width: 280 }}
          filter={optionMatcher}
          optionList={(quotesQuery.data?.items ?? []).map((q) => ({
            value: q.id,
            label: `${q.quote_no}（${q.customer_name ?? '-'}）`,
          }))}
        />
        <Select
          value={versionId}
          onChange={(v) => {
            setVersionId(v as number)
            setResult(null)
          }}
          placeholder="选版本"
          style={{ width: 140 }}
          optionList={(versionsQuery.data ?? []).map((v) => ({
            value: v.id,
            label: `V${v.version_no}（¥${Number(v.total_amount).toLocaleString('zh-CN')}）`,
          }))}
        />
        <Button
          theme="solid"
          disabled={!versionId}
          loading={sandboxMutation.isPending}
          onClick={() => sandboxMutation.mutate()}
        >
          试算
        </Button>
      </div>

      {result && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 8 }}>
            本单实况：
            {(result.context_fields ?? [])
              .filter((f) => result.context[f.field] !== undefined && !String(f.field).startsWith('_'))
              .map((f) => `${f.label} ${String(result.context[f.field])}${f.unit}`)
              .join(' ｜ ')}
          </div>
          <div style={{ display: 'grid', gap: 8 }}>
            {result.rules.map((rule) => (
              <div
                key={rule.rule_id}
                style={{
                  border: `1px solid ${rule.fired ? 'var(--crm-primary)' : 'var(--crm-surface-high)'}`,
                  borderRadius: 6,
                  padding: '8px 12px',
                  opacity: rule.enabled ? 1 : 0.55,
                }}
              >
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Tag color={rule.fired ? 'green' : 'grey'} size="small">
                    {rule.fired ? '命中并生效' : rule.matched ? '命中但未启用/被优先级更高规则截先' : '未命中'}
                  </Tag>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{rule.name}</span>
                  <Tag size="small">{RULE_KIND_LABEL[rule.kind] ?? rule.kind}</Tag>
                  {!rule.enabled && <Tag color="grey" size="small">已停用</Tag>}
                  {rule.effect && <span style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>{rule.effect}</span>}
                </div>
                <div style={{ fontSize: 12, marginTop: 4, display: 'grid', gap: 2 }}>
                  {rule.conditions_detail.map((detail, index) => (
                    <span key={index} style={{ color: detail.hit ? 'var(--crm-success)' : 'var(--crm-text-3)' }}>
                      {detail.hit ? '✓' : '✗'} {detail.label}：
                      实际 {detail.actual_label ?? String(detail.actual ?? '无数据')}，
                      要求 {detail.value_label ?? `${detail.op} ${String(detail.value)}`}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  )
}

const preStyle: React.CSSProperties = {
  fontSize: 12,
  background: 'var(--crm-surface-low)',
  borderRadius: 6,
  padding: 10,
  whiteSpace: 'pre-wrap',
  margin: 0,
}
