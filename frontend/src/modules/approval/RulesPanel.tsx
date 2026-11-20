import { formatBeijingTime } from '../../shared/beijing-time'
import { ApiError } from '../../shared/api/client'
import { useState, type ComponentProps } from 'react'
import {
  ConfirmRequiredModal,
  isConfirmRequired,
  confirmMessageOf,
} from '../../shared/components/ConfirmRequiredModal'
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
import GuardedButton from '../../shared/components/GuardedButton'
import { blockedReasons } from '../../shared/components/blocked-hint-reasons'
import { optionMatcher } from '../../shared/components/optionMatch'

type TagColor = ComponentProps<typeof Tag>['color']

const KIND_TONE: Record<string, TagColor> = {
  auto_pass: 'green',
  express: 'blue',
  exception_route: 'red',
}

//: 条件操作符的展示符号。**单一来源**：编辑器的下拉、列表的触发条件、
//: 版本详情、沙盒回退展示都从这里取，避免同一个 op 在四个地方写出四种样子。
//: 后端 `OPS_LABEL` 是权威文案（发布确认、conditions_pretty 用它的符号），
//: 这里只补前端自己拼文案时的兜底。
const OP_LABEL: Record<string, string> = {
  gte: '≥',
  gt: '>',
  lte: '≤',
  lt: '<',
  eq: '=',
  in: '属于',
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
  coSignLabel: string
}

const emptyDraft = (): DraftState => ({
  name: '',
  kind: 'auto_pass',
  priority: 100,
  description: '',
  conditions: [{ field: 'gross_margin', op: 'gte', value: 25 }],
  coSignLabel: '财务会签',
})

export default function RulesPanel() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<DraftState | null>(null)
  const [versionTarget, setVersionTarget] = useState<ApprovalRuleRow | null>(null)
  // 409 冲突提示：后端把「和哪条规则、哪两个阈值撞了」写在 message 里，
  // 这里原样展示。发布/启用失败时只弹这个，不关编辑器、不改表单。
  const [conflictNotice, setConflictNotice] = useState<{ title: string; message: string } | null>(
    null,
  )
  /* 保存草稿失败（409 冲突 / 422 校验）时，错误要贴在编辑弹窗里，
     而不是只闪一下 Toast —— 用户正在改的就是这张表单，原因得和输入框放在一起。
     同时不关弹窗、不动 drafts，用户按提示改完可以直接再保存。 */
  const [editorError, setEditorError] = useState<string | null>(null)
  const notifyConflict = (error: Error, verb: string): boolean => {
    const status = (error as ApiError).status
    const code = (error as ApiError).code
    if (status !== 409 && code !== 40901) return false
    setConflictNotice({ title: `规则冲突，未${verb}`, message: error.message })
    return true
  }

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
                add_node_permission: 'quote:finance_approve',
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
    onError: (error: Error) => {
      // 保留 editing / 表单输入，只在弹窗顶部回显后端原文
      setEditorError(error.message)
    },
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      toggleApprovalRule(id, enabled),
    onSuccess: () => refresh(),
    onError: (error: Error, variables) => {
      // 启用被 409 拦下：开关必须回到实际状态 —— 拉一次列表拿后端真实 enabled，
      // 不做乐观更新（否则会显示成"已经开了"）。
      if (!notifyConflict(error, variables.enabled ? '启用' : '停用')) Toast.error(error.message)
      refresh()
    },
  })

  // 发布审批规则需要先确认：发布之后**后续提交的报价审批都按新规则算**。
  // 后端不带 confirm 会返回 42206，并把「会发布哪一版、条件是什么」写在文案里；
  // 这里原样弹给用户，确认后带 confirm 重发。
  const [publishGate, setPublishGate] = useState<{ message: string; ruleId: number } | null>(
    null,
  )

  const publishMutation = useMutation<
    ApprovalRuleRow,
    Error,
    { id: number; confirm: boolean }
  >({
    mutationFn: ({ id, confirm }) => publishApprovalRule(id, confirm),
    onSuccess: (data) => {
      Toast.success(`已发布 V${data.published_version_no}，对新提交的审批生效`)
      refresh()
    },
    onError: (error: Error, variables) => {
      if (isConfirmRequired(error)) {
        setPublishGate({ message: confirmMessageOf(error), ruleId: variables.id })
      } else if (!notifyConflict(error, '发布')) {
        // 发布失败不清草稿：草稿是后端的 rule.conditions，前端列表随后 refresh，
        // 编辑器（如果开着）保持原样，用户改完阈值可以直接再发一次。
        Toast.error(error.message)
      }
    },
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
      className: 'rules-col-name',
      // 六列里只有「规则」伸缩：**不写 width**。固定布局下没写宽度的列
      // 自动吃掉剩余空间。原来写 width:'100%' 是错的 —— 那等于"这一列就要
      // 整张表那么宽"，再加上其余五列的固定宽，总宽必然超卡片：
      // 1440 下浏览器勉强摊平看不出来，1024 下「优先级/生效状态/操作」
      // 整段被推出卡片、只剩横向滚动条（2026-11-18 实拍）。
      // 说明文字允许换行（见 index.css .rules-table），窄屏折两行不丢字。
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
      className: 'rules-col-kind',
      dataIndex: 'kind',
      width: 80,
      render: (kind: string) => (
        <Tag color={(KIND_TONE[kind] ?? 'grey') as TagColor}>{RULE_KIND_LABEL[kind] ?? kind}</Tag>
      ),
    },
    {
      title: '触发条件',
      className: 'rules-col-cond',
      // 原来每条条件占一行：三条条件的规则行高被顶到 100px，四行规则就吃掉
      // 半屏（2026-11-20 截图）。条件之间本来是同一条规则的"与"，一行里用「·」
      // 连着读更顺；放不下就省略，悬停看全文。
      width: 330,
      render: (_: unknown, row: ApprovalRuleRow) => {
        const text = (row.conditions ?? [])
          .map((cond, index) => {
            const meta = fieldMeta(cond.field)
            return `${meta?.label ?? cond.field} ${
              row.conditions_pretty?.[index]?.value_label ??
              `${OP_LABEL[cond.op] ?? cond.op} ${String(cond.value)}`
            }`
          })
          .join(' · ')
        return (
          <div
            title={text}
            style={{
              fontSize: 12,
              color: 'var(--crm-text-2)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {text || '-'}
          </div>
        )
      },
    },
    { title: '优先级', className: 'rules-col-prio', dataIndex: 'priority', width: 56 },
    {
      title: '生效状态',
      className: 'rules-col-state',
      // 100：开关 + V1 胶囊一行放下；「草稿待发布」出现时换到第二行，
      // 不为了一个偶尔出现的标签把整列常驻加宽 30px。
      width: 100,
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap', gap: 6 }}>
          <Switch
            checked={row.enabled}
            size="small"
            onChange={(checked) => {
              Modal.confirm({
                title: `${checked ? '启用' : '停用'}审批规则？`,
                content: checked
                  ? `启用后，符合「${row.name}」条件的新报价审批会按此规则处理。`
                  : `停用后，符合「${row.name}」条件的新报价审批将不再使用此规则。`,
                onOk: () => toggleMutation.mutate({ id: row.id, enabled: checked }),
              })
            }}
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
      className: 'rules-col-ops',
      // 四个链接 4×24 + 3×4 间隙 = 108，加左右 8px 内边距（.rules-table）
      // 124 正好；内容居中，和表头同一条中线。
      width: 124,
      render: (_: unknown, row: ApprovalRuleRow) => (
        <div style={{ display: 'flex', gap: 4, fontSize: 12, justifyContent: 'center', whiteSpace: 'nowrap' }}>
          <a style={{ color: 'var(--crm-primary)' }} onClick={() => toEdit(row)}>
            编辑
          </a>
          <a style={{ color: 'var(--crm-success)' }} onClick={() => publishMutation.mutate({ id: row.id, confirm: false })}>
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
    setEditorError(null)
    setEditing({
      id: row.id,
      name: row.name,
      kind: row.kind,
      priority: row.priority,
      description: row.description ?? '',
      conditions: (row.conditions ?? []).map((c) => ({ ...c })),
      coSignLabel: String(row.action?.add_node_label ?? '财务会签'),
    })
  }

  return (
    <>
      <Modal
        visible={conflictNotice !== null}
        title={conflictNotice?.title ?? '规则冲突'}
        // 小屏不超出视口：与报价/样品详情弹窗同一写法（2026-11-18）
        width={Math.min(560, window.innerWidth - 24)}
        onCancel={() => setConflictNotice(null)}
        footer={
          <Button type="primary" onClick={() => setConflictNotice(null)}>
            知道了
          </Button>
        }
      >
        <div
          style={{
            fontSize: 13,
            lineHeight: 1.8,
            color: 'var(--crm-text-1)',
            padding: '2px 0 4px',
          }}
        >
          {conflictNotice?.message}
        </div>
        <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 10 }}>
          这次没有写入任何配置：开关/发布状态保持原样，草稿也还在。
        </div>
      </Modal>
      <ConfirmRequiredModal
        visible={publishGate !== null}
        message={publishGate?.message ?? ''}
        onCancel={() => setPublishGate(null)}
        onConfirm={() => {
          const ruleId = publishGate?.ruleId
          setPublishGate(null)
          if (ruleId !== undefined) publishMutation.mutate({ id: ruleId, confirm: true })
        }}
      />
      <SectionCard>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
            报价提交审批时按优先级逐条求值，第一条命中的规则决定走向；
            草稿发布后才生效，启停立即生效
          </div>
          <Button
            type="primary"
            theme="outline"
            onClick={() => {
              setEditorError(null)
              setEditing(emptyDraft())
            }}
          >
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
          // 默认是 auto 布局：写死的列宽只是"起步宽度"，长文案会把列顶开，
          // 「操作」整列被推到卡片外、只剩横向滚动条（2026-11-18）。
          // 固定布局让表格按卡片宽度摊平；不要再挂 scroll.x，
          // 那会让 Semi 常驻一条空滚动条，看着像没对齐（2026-11-18）。
          tableLayout="fixed"
          className="rules-table"
        />
        {/* 表格不挂分页条，条数只能自己说一句 */}
        {Boolean(rulesQuery.data?.length) && (
          <div style={{ marginTop: 10, textAlign: 'right', fontSize: 12, color: 'var(--crm-text-3)' }}>
            共 {rulesQuery.data?.length} 条规则
          </div>
        )}
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
          setEditorError(null)
          saveMutation.mutate(editing)
        }}
        confirmLoading={saveMutation.isPending}
        width={Math.min(680, window.innerWidth - 24)}
        okText="保存草稿"
      >
        {editing && (
          <div style={{ display: 'grid', gap: 14 }}>
            {editorError && (
              <div
                style={{
                  background: '#fff1f0',
                  border: '1px solid #ffccc7',
                  borderRadius: 6,
                  padding: '8px 12px',
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600, color: '#cf1322', marginBottom: 2 }}>
                  保存失败，未写入
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.7, color: '#a8071a', whiteSpace: 'pre-wrap' }}>
                  {editorError}
                </div>
                <div style={{ fontSize: 12, color: '#a8071a', marginTop: 4, opacity: 0.85 }}>
                  表单内容已保留，按上面提示改完再保存。
                </div>
              </div>
            )}
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
                          label: OP_LABEL[op] ?? op,
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
                  <div style={labelStyle}>会签资格</div>
                  <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>拥有「财务会签」权限即可，不限制角色。授权在「系统设置 → 角色与数据范围」维护。</div>
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

      <VersionDrawer rule={versionTarget} fields={fields} onClose={() => setVersionTarget(null)} />
    </>
  )
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--crm-text-3)',
  marginBottom: 4,
}

// ---------------------------------------------------------------- 版本历史

function VersionDrawer({
  rule,
  fields,
  onClose,
}: {
  rule: ApprovalRuleRow | null
  // 字段中文名来自同一个字段元数据接口 —— 版本快照里只存 `field` 代码，
  // 不把代码翻成中文就会变成"给人看的 JSON"，和"不显示原始 JSON"的要求冲突。
  fields: ConditionFieldMeta[]
  onClose: () => void
}) {
  const versionsQuery = useQuery({
    queryKey: ['approval-rule-versions', rule?.id],
    queryFn: () => listApprovalRuleVersions(rule!.id),
    enabled: Boolean(rule),
  })
  return (
    <Modal
      title={rule ? `版本历史：${rule.name}` : ''}
      visible={Boolean(rule)}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {/* 原来每条版本都包一张 SectionCard：外层弹窗一层内边距、卡片再一层，
          左空右空、三个「标签：值」挤在一条线里，看着就是没排版的灰字（主人
          2026-11-18："这弹窗太丑了"）。现在改成：版本号做徽标、公布时间靠右、
          中间一条极淡的分隔线，下面四行按「标签列 + 值列」两栏对齐。 */}
      <div style={{ display: 'grid', gap: 12, maxHeight: 440, overflow: 'auto', paddingRight: 2 }}>
        {(versionsQuery.data ?? []).map((version) => (
          <div
            key={version.id}
            style={{
              border: '1px solid var(--crm-border)',
              borderRadius: 10,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 14px',
                background: 'var(--crm-surface-low)',
                borderBottom: '1px solid var(--crm-border)',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  height: 22,
                  padding: '0 9px',
                  borderRadius: 11,
                  background: 'var(--crm-primary-soft)',
                  color: 'var(--crm-primary)',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                V{version.version_no}
              </span>
              {version.is_current && (
                <Tag color="cyan" size="small">
                  当前生效
                </Tag>
              )}
              <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--crm-text-3)' }}>
                {formatBeijingTime(version.published_at)}
              </span>
            </div>
            {/* 三行改四行：规则类型、优先级、触发条件、命中后，标签列固定 76px，
                值列左对齐 —— 眼睛竖着扫一遍就能对上，不用在句子里找冒号。 */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '76px minmax(0,1fr)',
                rowGap: 9,
                columnGap: 10,
                padding: '12px 14px',
                fontSize: 13,
                lineHeight: 1.7,
              }}
            >
              <span style={{ color: 'var(--crm-text-3)' }}>规则类型</span>
              <span>{RULE_KIND_LABEL[version.payload.kind] ?? '审批规则'}</span>
              <span style={{ color: 'var(--crm-text-3)' }}>优先级</span>
              <span>
                {version.payload.priority}
                <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 6 }}>
                  数字小的先算
                </span>
              </span>
              <span style={{ color: 'var(--crm-text-3)' }}>触发条件</span>
              <span>
                {(version.payload.conditions ?? []).length === 0
                  ? '没有条件，整单适用'
                  : (version.payload.conditions ?? []).map((cond, index) => {
                      const meta = fields.find((f) => f.field === cond.field)
                      const symbol = OP_LABEL[cond.op] ?? cond.op
                      const value =
                        typeof cond.value === 'boolean'
                          ? cond.value
                            ? '是'
                            : '否'
                          : String(cond.value ?? '-')
                      return (
                        <span key={index}>
                          {index > 0 && (
                            <span style={{ color: 'var(--crm-text-3)', margin: '0 6px' }}>且</span>
                          )}
                          {meta?.label ?? '未命名字段'} {symbol} {value}
                        </span>
                      )
                    })}
              </span>
              <span style={{ color: 'var(--crm-text-3)' }}>命中后</span>
              <span>
                {KIND_HINT[version.payload.kind] ?? '按规则执行'}
                {version.payload.kind === 'exception_route' && (
                  <span style={{ color: 'var(--crm-text-3)' }}>
                    （会签节点：{String(version.payload.action?.add_node_label ?? '财务会签')}）
                  </span>
                )}
              </span>
            </div>
          </div>
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

  // 主人 2026-10-12 反馈（原话）：「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，
  // 类似的情况都要修」。这里的「试算」原来就是这样：没选报价单时它灰着，「选版本」下拉也是空的
  // 且**照样能点开**（点开还是空的），整块区域一句话都不说；选了报价单但那张报价还没有版本时同样。
  // 判据一个字没动（按钮仍是 `!versionId` 才灰）。空态说明照同文件 VersionDrawer 的
  // 「还没有发布过版本」那一处补，规矩见 shared/components/BlockedHint.tsx。
  const versions = versionsQuery.data ?? []
  const versionsLoading = Boolean(quoteId) && versionsQuery.isPending
  const versionsFailed = Boolean(quoteId) && versionsQuery.isError
  const versionsEmpty = Boolean(quoteId) && versionsQuery.isSuccess && versions.length === 0
  const sandboxBlocked = !versionId
  const sandboxBlockedReasons = blockedReasons([
    // 顺序就是用户的操作顺序：先选报价单 → 等版本读出来 → 再挑一个版本
    { ok: Boolean(quoteId), reason: '请先选择报价单，再选版本试算。' },
    { ok: !versionsLoading, reason: '正在读取这张报价的版本，请稍候…' },
    // 读失败不能算成「还没有版本」：那会让人白跑一趟去建一版，问题却还在
    {
      ok: !versionsEmpty && !versionsFailed,
      reason: versionsFailed
        ? '这张报价的版本列表没读出来，请刷新页面重试。'
        : '这张报价还没有版本，先去报价中心建一版再回来试算。',
    },
    // 报价单和版本列表都就绪、只是还没挑版本：按钮同样是灰的，一并说清楚
    {
      ok: Boolean(versionId) || !quoteId || versionsLoading || versionsEmpty || versionsFailed,
      reason: '请在「选版本」里选一个版本，再点试算。',
    },
  ])
  // ① 看得见（按钮下面常驻一段说明）+ ② 点得动（灰按钮外面包一层接点击）。
  // 禁用按钮用的是原生 `disabled`，浏览器不派发点击事件，所以禁用态要给它加
  // `pointerEvents:'none'` 让点击落到外层容器上（同 SampleFromSourceModal / PriceCenterPage）。

  return (
    <SectionCard style={{ marginTop: 16 }}>
      {/* 原来标题后面挂一句括注、下面一行四个控件挤在一起：报价下拉只有 280
          宽、长客户名被截成「【演…」，试算的灰字还被塞进按钮那一列、折成三行
          （2026-11-18 主人："太丑了"）。改成：标题一行、说明另起一行小字，
          控件按「搜索 / 报价 / 版本 / 按钮」四列排，说明落到整行下面。 */}
      <div style={{ fontWeight: 600, fontSize: 14 }}>规则沙盒</div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4, marginBottom: 12 }}>
        发布前先试算这套规则会命中哪几条，不动任何数据
      </div>
      <div className="sandbox-row">
        <Input
          value={keyword}
          onChange={setKeyword}
          placeholder="搜报价单号/客户"
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
          optionList={(versionsQuery.data ?? []).map((v) => ({
            value: v.id,
            label: `V${v.version_no}（¥${Number(v.total_amount).toLocaleString('zh-CN')}）`,
          }))}
        />
        {/* 灰按钮要能解释自己（见 BlockedHint.tsx）：禁用态给按钮加 pointerEvents:'none'，
            按钮对点击透明，落点变成外层容器，容器上的 onClick 才接得住、才弹得出原因。 */}
        <GuardedButton
          theme="solid"
          // hintBelow：说明落到整行下面占满宽，不再挤在按钮那一列里折三行
          hintBelow
          disabled={sandboxBlocked}
          blockedReasons={sandboxBlockedReasons}
          action="试算"
          loading={sandboxMutation.isPending}
          onClick={() => sandboxMutation.mutate()}
        >
          试算
        </GuardedButton>
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
