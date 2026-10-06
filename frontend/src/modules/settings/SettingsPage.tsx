import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Tabs, Tag } from '@douyinfe/semi-ui'

import {
  createDepartment,
  createRole,
  createUser,
  deleteDepartment,
  deleteRole,
  disableUser,
  enableUser,
  listDepartments,
  listPermissions,
  listRoles,
  listUsers,
  setUserRoles,
  updateDepartment,
  updateRole,
  updateUser,
} from '../../shared/api/system'
import { createTag, deleteTag, listTags, updateTag, type TagRow } from '../../shared/api/customer'
import { listAuditLogs, type AuditLogRow } from '../../shared/api/analytics'
import PageHeader from '../../shared/components/PageHeader'
import {
  decideRecycleCandidate,
  listPublicPoolRules,
  listRecycleCandidates,
  listSettings,
  listTaskRules,
  runAutoTaskRules,
  runPublicPoolRecycle,
  saveSetting,
  updatePublicPoolRule,
  updateTaskRule,
  type PublicPoolRuleRow,
  type RecycleCandidateRow,
  type SystemSettingRow,
  type TaskRuleRow,
} from '../../shared/api/settings'
import { Button, Input, Modal, Popconfirm, Select, Switch, Toast } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import type { SystemDepartment, SystemRole, SystemUser } from '../../shared/api/system'
import NotificationSettingsPanel from './NotificationSettingsPanel'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'

const TABS = [
  { tab: '用户', itemKey: 'users' },
  { tab: '角色与数据范围', itemKey: 'roles' },
  { tab: '部门', itemKey: 'departments' },
  { tab: '客户标签', itemKey: 'tags' },
  { tab: '通知', itemKey: 'notifications' },
  { tab: '审计日志', itemKey: 'audit' },
  { tab: '业务规则', itemKey: 'rules' },
]

/** 时间显示成"2026-10-06 12:30"（回收复核要看具体哪天）。空值给个短横。 */
function fmtDay(value: string | null | undefined): string {
  if (!value) return '-'
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return String(value).slice(0, 16).replace('T', ' ')
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`
}

export default function SettingsPage() {
  // 支持深链：/settings?tab=rules
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeKey, setActiveKey] = useState(searchParams.get('tab') ?? 'users')
  const { isAdmin } = usePermissions()

  const usersQuery = useQuery({
    queryKey: ['settings-users'],
    queryFn: () => listUsers({ page: 1, page_size: 50 }),
    enabled: isAdmin,
  })
  const rolesQuery = useQuery({ queryKey: ['settings-roles'], queryFn: listRoles, enabled: isAdmin })
  const deptsQuery = useQuery({
    queryKey: ['settings-departments'],
    queryFn: listDepartments,
    enabled: isAdmin,
  })
  const auditQuery = useQuery({
    queryKey: ['audit-logs', activeKey],
    queryFn: () => listAuditLogs({ page: 1, page_size: 50 }),
    enabled: isAdmin && activeKey === 'audit',
  })
  const queryClient = useQueryClient()
  const poolRulesQuery = useQuery({
    queryKey: ['public-pool-rules'],
    queryFn: listPublicPoolRules,
    enabled: isAdmin && activeKey === 'rules',
  })
  // 回收候选（预告）：主管逐条复核的对象（返工单 6.3）
  const [candidatePage, setCandidatePage] = useState(1)
  const candidatesQuery = useQuery({
    queryKey: ['recycle-candidates', candidatePage],
    queryFn: () => listRecycleCandidates({ status: 'pending', page: candidatePage, page_size: 20 }),
    enabled: isAdmin && activeKey === 'rules',
  })
  const taskRulesQuery = useQuery({
    queryKey: ['task-rules'],
    queryFn: listTaskRules,
    enabled: isAdmin && activeKey === 'rules',
  })
  const settingsQuery = useQuery({
    queryKey: ['system-settings'],
    queryFn: listSettings,
    enabled: isAdmin && activeKey === 'rules',
  })

  // 客户标签字典（03-API §7 的 /tags）
  const tagsQuery = useQuery({
    queryKey: ['settings-tags'],
    queryFn: () => listTags(false),
    enabled: activeKey === 'tags',
  })
  const [tagForm, setTagForm] = useState<{ name: string; type: string }>({ name: '', type: 'custom' })
  const [tagEditTarget, setTagEditTarget] = useState<TagRow | null>(null)

  const refreshTags = () => void queryClient.invalidateQueries({ queryKey: ['settings-tags'] })

  const createTagMutation = useMutation({
    mutationFn: () => createTag({ name: tagForm.name, type: tagForm.type }),
    onSuccess: () => {
      Toast.success('标签已创建')
      setTagForm({ name: '', type: 'custom' })
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const updateTagMutation = useMutation({
    mutationFn: () =>
      updateTag(tagEditTarget!.id, { name: tagEditTarget!.name, type: tagEditTarget!.type }),
    onSuccess: () => {
      Toast.success('标签已保存')
      setTagEditTarget(null)
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteTagMutation = useMutation({
    mutationFn: (id: number) => deleteTag(id),
    onSuccess: () => {
      Toast.success('标签已删除')
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const refreshRules = () => {
    void queryClient.invalidateQueries({ queryKey: ['public-pool-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['task-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['system-settings'] })
  }

  const poolRuleMutation = useMutation({
    mutationFn: ({ id, days, enabled }: { id: number; days: number; enabled: boolean }) =>
      updatePublicPoolRule(id, { days, enabled }),
    onSuccess: () => {
      Toast.success('公海规则已保存')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const taskRuleMutation = useMutation({
    mutationFn: ({ id, days }: { id: number; days: number }) => {
      const rule = (taskRulesQuery.data ?? []).find((item) => item.id === id)
      return updateTaskRule(id, {
        trigger_config: { ...(rule?.trigger_config ?? {}), days },
      })
    },
    onSuccess: () => {
      Toast.success('自动任务规则已保存')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const settingMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: Record<string, unknown> }) =>
      saveSetting(key, value),
    onSuccess: () => {
      Toast.success('配置已保存')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const recycleMutation = useMutation({
    mutationFn: runPublicPoolRecycle,
    onSuccess: (data) => {
      // 文案要说清"提名 ≠ 回收"：老版本这里写的是"已回收 N 个客户"，
      // 现在扫描只提名，回收要等主管批（返工单 6.3）
      Toast.success(
        `扫描完成：提名 ${data.nominated_count} 个待复核`
          + (data.protected_count ? `，${data.protected_count} 个在履约中已豁免` : ''),
      )
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const decideMutation = useMutation({
    mutationFn: ({ id, decision, note }: {
      id: number
      decision: 'approve' | 'reject' | 'defer'
      note?: string
    }) => decideRecycleCandidate(id, decision, note),
    onSuccess: (row) => {
      Toast.success(`已${row.status_label}`)
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /** 批准/驳回/暂缓。批准和暂缓可能要填说明（尤其"仍有保护但确需回收"时必须填）。 */
  const decide = (decision: 'approve' | 'reject' | 'defer', row: RecycleCandidateRow) => {
    const label = { approve: '批准回收', reject: '驳回', defer: '暂缓' }[decision]
    const needNote = decision !== 'approve' || (row.protection ?? []).length > 0
    if (!needNote) {
      decideMutation.mutate({ id: row.id, decision })
      return
    }
    const note = window.prompt(
      decision === 'approve' && (row.protection ?? []).length > 0
        ? `该客户仍有履约事项（${(row.protection ?? []).join('；')}）。`
          + '确需例外回收，请填写原因（会记入审计）：'
        : `请填写${label}原因（会记入审计）：`,
    )
    if (note === null) return
    if (!note.trim()) {
      Toast.error(`请填写${label}原因`)
      return
    }
    decideMutation.mutate({ id: row.id, decision, note: note.trim() })
  }

  const autoTaskMutation = useMutation({
    mutationFn: runAutoTaskRules,
    onSuccess: (data) => {
      if (data.failed_rule_count) {
        Toast.warning(`生成 ${data.created_count} 条任务；${data.failed_rule_count} 条规则配置有误：${data.rule_errors?.map((row) => `${row.code}：${row.error}`).join('；') ?? '请查看审计记录'}`)
      } else {
        Toast.success(`已执行，生成 ${data.created_count} 条自动任务`)
      }
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 用户 / 角色 / 部门 的增删改（03-API §3 / §4 / §5） ----
  const [userModal, setUserModal] = useState(false)
  const [userEditTarget, setUserEditTarget] = useState<SystemUser | null>(null)
  const [userForm, setUserForm] = useState<{
    name: string
    username: string
    password: string
    mobile: string
    email: string
    department_id?: number
    role_ids: number[]
  }>({ name: '', username: '', password: '', mobile: '', email: '', role_ids: [] })

  const [roleModal, setRoleModal] = useState(false)
  const [roleEditTarget, setRoleEditTarget] = useState<SystemRole | null>(null)
  const [roleForm, setRoleForm] = useState<{
    code: string
    name: string
    description: string
    data_scope: string
    permission_codes: string[]
  }>({ code: '', name: '', description: '', data_scope: 'self', permission_codes: [] })

  const [deptModal, setDeptModal] = useState(false)
  const [deptEditTarget, setDeptEditTarget] = useState<SystemDepartment | null>(null)
  const [deptForm, setDeptForm] = useState<{
    name: string
    parent_id?: number
    wecom_department_id: string
  }>({ name: '', wecom_department_id: '' })

  const permissionsQuery = useQuery({
    queryKey: ['settings-permissions'],
    queryFn: listPermissions,
    enabled: roleModal,
  })

  const refreshUsers = () => void queryClient.invalidateQueries({ queryKey: ['settings-users'] })
  const refreshRoles = () => {
    void queryClient.invalidateQueries({ queryKey: ['settings-roles'] })
    void queryClient.invalidateQueries({ queryKey: ['assignable-users'] })
  }
  const refreshDepts = () => void queryClient.invalidateQueries({ queryKey: ['settings-departments'] })

  const userSaveMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {
        name: userForm.name,
        mobile: userForm.mobile || null,
        email: userForm.email || null,
        department_id: userForm.department_id ?? null,
      }
      if (userEditTarget) {
        // 编辑时不传 username（登录名不可改），密码留空表示不改
        if (userForm.password) payload.password = userForm.password
        return updateUser(userEditTarget.id, payload)
      }
      return createUser({
        ...payload,
        username: userForm.username,
        password: userForm.password,
        role_ids: userForm.role_ids,
      })
    },
    onSuccess: async () => {
      // 编辑时顺带同步角色
      if (userEditTarget) {
        await setUserRoles(userEditTarget.id, userForm.role_ids)
      }
      Toast.success(userEditTarget ? '用户已保存' : '用户已创建')
      setUserModal(false)
      refreshUsers()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const userStatusMutation = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      enable ? enableUser(id) : disableUser(id),
    onSuccess: (_data, variables) => {
      Toast.success(variables.enable ? '账号已启用' : '账号已停用')
      refreshUsers()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const roleSaveMutation = useMutation({
    mutationFn: () =>
      roleEditTarget
        ? updateRole(roleEditTarget.id, {
            name: roleForm.name,
            description: roleForm.description || null,
            data_scope: roleForm.data_scope,
            permission_codes: roleForm.permission_codes,
          })
        : createRole({
            code: roleForm.code,
            name: roleForm.name,
            description: roleForm.description || null,
            data_scope: roleForm.data_scope,
            permission_codes: roleForm.permission_codes,
          }),
    onSuccess: () => {
      Toast.success(roleEditTarget ? '角色已保存' : '角色已创建')
      setRoleModal(false)
      refreshRoles()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteRoleMutation = useMutation({
    mutationFn: (id: number) => deleteRole(id),
    onSuccess: () => {
      Toast.success('角色已删除')
      refreshRoles()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deptSaveMutation = useMutation({
    mutationFn: () =>
      deptEditTarget
        ? updateDepartment(deptEditTarget.id, {
            name: deptForm.name,
            parent_id: deptForm.parent_id ?? null,
            wecom_department_id: deptForm.wecom_department_id || null,
          })
        : createDepartment({
            name: deptForm.name,
            parent_id: deptForm.parent_id ?? null,
            wecom_department_id: deptForm.wecom_department_id || null,
          }),
    onSuccess: () => {
      Toast.success(deptEditTarget ? '部门已保存' : '部门已创建')
      setDeptModal(false)
      refreshDepts()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteDeptMutation = useMutation({
    mutationFn: (id: number) => deleteDepartment(id),
    onSuccess: () => {
      Toast.success('部门已删除')
      refreshDepts()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  if (!isAdmin) {
    return (
      <div className="page-container">
        <PageHeader title="系统设置" />
        <div className="placeholder-box">只有管理员可以查看用户、角色和部门配置。</div>
      </div>
    )
  }

  const deptOptions = (deptsQuery.data ?? []).map((dept) => ({
    value: dept.id,
    label: dept.name,
  }))
  const roleOptions = (rolesQuery.data ?? []).map((role) => ({
    value: role.id,
    label: `${role.name}（${role.code}）`,
  }))
  const scopeOptions = [
    { value: 'self', label: '仅本人' },
    { value: 'department', label: '本部门' },
    { value: 'department_and_sub', label: '本部门及下级' },
    { value: 'all', label: '全部' },
  ]

  const userColumns = [
    { title: '姓名', dataIndex: 'name', width: 140 },
    { title: '登录名', dataIndex: 'username', width: 150 },
    { title: '部门', dataIndex: 'department', width: 140, render: (v: string | null) => v ?? '-' },
    {
      title: '角色',
      dataIndex: 'roles',
      width: 180,
      render: (roles: SystemUser['roles']) =>
        (roles ?? []).length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        ) : (
          <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>
            {(roles ?? []).map((role) => (
              <Tag key={role.id} size="small">
                {role.name}
              </Tag>
            ))}
          </span>
        ),
    },
    { title: '手机', dataIndex: 'mobile', width: 140, render: (v: string | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '操作',
      width: 160,
      render: (_: unknown, record: SystemUser) => (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setUserEditTarget(record)
              setUserForm({
                name: record.name,
                username: record.username,
                password: '',
                mobile: record.mobile ?? '',
                email: record.email ?? '',
                department_id: record.department_id ?? undefined,
                role_ids: (record.roles ?? []).map((role) => role.id),
              })
              setUserModal(true)
            }}
          >
            编辑
          </Button>
          <Button
            theme="borderless"
            size="small"
            type={record.status === 'active' ? 'danger' : 'primary'}
            onClick={() =>
              userStatusMutation.mutate({ id: record.id, enable: record.status !== 'active' })
            }
          >
            {record.status === 'active' ? '停用' : '启用'}
          </Button>
        </>
      ),
    },
  ]

  const roleColumns = [
    { title: '角色码', dataIndex: 'code', width: 150 },
    { title: '角色名', dataIndex: 'name', width: 130 },
    {
      title: '数据范围',
      dataIndex: 'data_scope',
      width: 130,
      render: (v: string) =>
        ({ self: '仅本人', department: '本部门', department_and_sub: '本部门及下级', all: '全部' })[
          v
        ] ?? v,
    },
    {
      title: '权限数',
      dataIndex: 'permission_codes',
      width: 90,
      render: (codes: string[] | undefined) => (codes ?? []).length,
    },
    { title: '说明', dataIndex: 'description', render: (v: string | null) => v ?? '-' },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, record: SystemRole) => (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setRoleEditTarget(record)
              setRoleForm({
                code: record.code,
                name: record.name,
                description: record.description ?? '',
                data_scope: record.data_scope,
                permission_codes: record.permission_codes ?? [],
              })
              setRoleModal(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除角色「${record.name}」？`}
            onConfirm={() => deleteRoleMutation.mutate(record.id)}
          >
            <Button theme="borderless" type="danger" size="small">
              删除
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  const deptColumns = [
    { title: '部门名称', dataIndex: 'name' },
    {
      title: '上级部门',
      dataIndex: 'parent_id',
      width: 140,
      render: (v: number | null) =>
        v ? (deptOptions.find((d) => d.value === v)?.label ?? `#${v}`) : '顶级',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => (v === 'active' ? '启用' : '停用'),
    },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, record: SystemDepartment) => (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setDeptEditTarget(record)
              setDeptForm({
                name: record.name,
                parent_id: record.parent_id ?? undefined,
                wecom_department_id: record.wecom_department_id ?? '',
              })
              setDeptModal(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除部门「${record.name}」？下级部门或用户非空时会拒绝。`}
            onConfirm={() => deleteDeptMutation.mutate(record.id)}
          >
            <Button theme="borderless" type="danger" size="small">
              删除
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="系统设置"
        subtitle="用户、角色、部门可在此维护；业务规则、自动任务与系统配置可直接修改"
      />
      <SectionCard>
        <Tabs
          type="line"
          activeKey={activeKey}
          onChange={(key) => {
            setActiveKey(key)
            setSearchParams({ tab: key })
          }}
          tabList={TABS}
        />
        <div style={{ marginTop: 16 }}>
          {activeKey === 'users' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button
                  theme="solid"
                  onClick={() => {
                    setUserEditTarget(null)
                    setUserForm({
                      name: '',
                      username: '',
                      password: '',
                      mobile: '',
                      email: '',
                      role_ids: [],
                    })
                    setUserModal(true)
                  }}
                >
                  新建用户
                </Button>
              </div>
              <Table<SystemUser>
                columns={userColumns}
                dataSource={usersQuery.data?.items ?? []}
                loading={usersQuery.isLoading}
                rowKey="id"
                pagination={false}
              />
            </>
          )}
          {activeKey === 'roles' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button
                  theme="solid"
                  onClick={() => {
                    setRoleEditTarget(null)
                    setRoleForm({
                      code: '',
                      name: '',
                      description: '',
                      data_scope: 'self',
                      permission_codes: [],
                    })
                    setRoleModal(true)
                  }}
                >
                  新建角色
                </Button>
              </div>
              <Table<SystemRole>
                columns={roleColumns}
                dataSource={rolesQuery.data ?? []}
                loading={rolesQuery.isLoading}
                rowKey="id"
                pagination={false}
              />
            </>
          )}
          {activeKey === 'departments' && (
            <>
              <div className="toolbar">
                <div style={{ flex: 1 }} />
                <Button
                  theme="solid"
                  onClick={() => {
                    setDeptEditTarget(null)
                    setDeptForm({ name: '', wecom_department_id: '' })
                    setDeptModal(true)
                  }}
                >
                  新建部门
                </Button>
              </div>
              <Table<SystemDepartment>
                columns={deptColumns}
                dataSource={deptsQuery.data ?? []}
                loading={deptsQuery.isLoading}
                rowKey="id"
                pagination={false}
              />
            </>
          )}

          {activeKey === 'tags' && (
            <>
              <div className="toolbar">
                <Input
                  style={{ width: 200 }}
                  placeholder="标签名称"
                  value={tagForm.name}
                  onChange={(value) => setTagForm({ ...tagForm, name: value })}
                />
                <Select
                  style={{ width: 150 }}
                  value={tagForm.type}
                  onChange={(value) => setTagForm({ ...tagForm, type: value as string })}
                  optionList={[
                    { value: 'custom', label: '自定义' },
                    { value: 'region', label: '区域' },
                    { value: 'industry', label: '行业' },
                    { value: 'grade', label: '等级' },
                    { value: 'channel', label: '渠道' },
                  ]}
                />
                <Button
                  theme="solid"
                  loading={createTagMutation.isPending}
                  disabled={!tagForm.name.trim()}
                  onClick={() => createTagMutation.mutate()}
                >
                  新建标签
                </Button>
                <div style={{ flex: 1 }} />
                <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  标签是受控字典，客户详情里只能从这里维护的标签中选择
                </span>
              </div>
              <Table<TagRow>
                columns={[
                  { title: '标签名', dataIndex: 'name' },
                  { title: '分组', dataIndex: 'type', width: 120 },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 100,
                    render: (value: string) => (
                      <Tag size="small" color={value === 'active' ? 'green' : 'grey'}>
                        {value === 'active' ? '启用' : '停用'}
                      </Tag>
                    ),
                  },
                  {
                    title: '操作',
                    width: 150,
                    render: (_: unknown, record: TagRow) => (
                      <>
                        <Button
                          theme="borderless"
                          size="small"
                          onClick={() => setTagEditTarget(record)}
                        >
                          编辑
                        </Button>
                        <Popconfirm
                          title="删除标签会同时移除所有客户上的这个标签，确认？"
                          onConfirm={() => deleteTagMutation.mutate(record.id)}
                        >
                          <Button theme="borderless" type="danger" size="small">
                            删除
                          </Button>
                        </Popconfirm>
                      </>
                    ),
                  },
                ]}
                dataSource={tagsQuery.data ?? []}
                loading={tagsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有标签"
              />
            </>
          )}
          {activeKey === 'notifications' && <NotificationSettingsPanel />}
          {activeKey === 'audit' && (
            <Table<AuditLogRow>
              columns={[
                {
                  title: '时间',
                  dataIndex: 'created_at',
                  width: 190,
                  render: (v: string) => new Date(v).toLocaleString('zh-CN'),
                },
                {
                  title: '操作人',
                  dataIndex: 'operator_name',
                  width: 110,
                  render: (v: string | null) => v ?? '系统',
                },
                {
                  title: '对象',
                  width: 200,
                  render: (_: unknown, record: AuditLogRow) =>
                    record.business_type
                      ? `${record.business_type} #${record.business_id ?? '-'}`
                      : '-',
                },
                { title: '动作', dataIndex: 'action', width: 130 },
                { title: '来源', dataIndex: 'source', width: 110 },
                {
                  title: '变更内容',
                  render: (_: unknown, record: AuditLogRow) => {
                    const text = record.after_data
                      ? JSON.stringify(record.after_data)
                      : record.before_data
                        ? JSON.stringify(record.before_data)
                        : '-'
                    return (
                      <span style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
                        {text.length > 90 ? `${text.slice(0, 90)}…` : text}
                      </span>
                    )
                  },
                },
                {
                  title: 'IP',
                  dataIndex: 'ip',
                  width: 130,
                  render: (v: string | null) => v ?? '-',
                },
              ]}
              dataSource={auditQuery.data?.items ?? []}
              loading={auditQuery.isLoading}
              rowKey="id"
              pagination={false}
              empty="还没有审计记录"
              scroll={{ x: 1200 }}
            />
          )}
          {activeKey === 'rules' && (
            <div style={{ display: 'grid', gap: 20 }}>
              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>公海回收规则</div>
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 12 }}>
                  客户超过设定天数没有跟进记录，就会被提名成回收候选。
                  回收不会自动发生 —— 要先预告、再由主管逐条或批量批准；
                  批准时还会再检查一遍这期间有没有新的跟进、报价、订单或回款。
                  客户手上还有在途订单、未结应收、有效正式报价或在途打样时会被保护。
                </div>
                <Table<PublicPoolRuleRow>
                  columns={[
                    { title: '客户等级', dataIndex: 'level', width: 110, render: (v: string) => `${v} 级` },
                    {
                      title: '未跟进天数',
                      dataIndex: 'days',
                      width: 220,
                      render: (days: number, record: PublicPoolRuleRow) => (
                        <Input
                          defaultValue={String(days)}
                          style={{ width: 120 }}
                          onBlur={(event) => {
                            const raw = (event.target as HTMLInputElement).value.trim()
                            const next = Number(raw)
                            if (!raw || !Number.isInteger(next) || next <= 0) {
                              Toast.warning('公海回收天数须为正整数')
                              return
                            }
                            if (Number.isFinite(next) && next !== days) {
                              poolRuleMutation.mutate({ id: record.id, days: next, enabled: record.enabled })
                            }
                          }}
                        />
                      ),
                    },
                    {
                      title: '启用',
                      dataIndex: 'enabled',
                      width: 100,
                      render: (enabled: boolean, record: PublicPoolRuleRow) => (
                        <Switch
                          checked={enabled}
                          onChange={(value) =>
                            poolRuleMutation.mutate({ id: record.id, days: record.days, enabled: value })
                          }
                        />
                      ),
                    },
                    { title: '说明', dataIndex: 'remark', render: (v: string | null) => v ?? '-' },
                  ]}
                  dataSource={poolRulesQuery.data ?? []}
                  loading={poolRulesQuery.isLoading}
                  rowKey="id"
                  pagination={false}
                />
                <div style={{ marginTop: 12 }}>
                  <Button
                    onClick={() => recycleMutation.mutate()}
                    loading={recycleMutation.isPending}
                  >
                    扫描并生成回收预告
                  </Button>
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 12 }}>
                    正式运行由定时任务触发；这里只**提名**候选，不会直接回收
                  </span>
                </div>
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>回收待复核</div>
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 12 }}>
                  这些客户按规则该回收了，但还没真的回收。逐条或批量批准后才执行；
                  点「驳回」表示不该回收，点「暂缓」表示过一阵再看。
                </div>
                <Table<RecycleCandidateRow>
                  rowKey="id"
                  loading={candidatesQuery.isLoading}
                  dataSource={candidatesQuery.data?.items ?? []}
                  pagination={{
                    currentPage: candidatePage,
                    pageSize: 20,
                    total: candidatesQuery.data?.total ?? 0,
                    onPageChange: setCandidatePage,
                  }}
                  empty="没有待复核的回收候选"
                  columns={[
                    {
                      title: '客户',
                      dataIndex: 'customer_name',
                      render: (v: string | null, r: RecycleCandidateRow) => (
                        <span>
                          {v ?? `#${r.customer_id}`}
                          <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 6 }}>
                            {r.level ? `${r.level} 级` : ''}
                            {r.rule_days ? ` · 超 ${r.rule_days} 天未跟进` : ''}
                          </span>
                        </span>
                      ),
                    },
                    {
                      title: '原负责人',
                      dataIndex: 'owner_name',
                      width: 110,
                      render: (v: string | null) => v ?? '-',
                    },
                    {
                      // 复核要看得到"是按哪个时间判它冷落的"，不是只给一句"已超 N 天"
                      title: '最近有效联系',
                      dataIndex: 'last_active_at',
                      width: 150,
                      render: (v: string | null) => fmtDay(v),
                    },
                    {
                      title: '保护事项',
                      dataIndex: 'protection',
                      render: (list: string[]) =>
                        (list ?? []).length === 0 ? (
                          <span style={{ color: 'var(--crm-text-3)' }}>无</span>
                        ) : (
                          <span style={{ color: 'var(--crm-caution, #b26a00)' }}>
                            {(list ?? []).join('；')}
                          </span>
                        ),
                    },
                    {
                      title: '预告到期',
                      dataIndex: 'due_at',
                      width: 130,
                      render: (v: string | null) => fmtDay(v),
                    },
                    {
                      title: '操作',
                      width: 190,
                      render: (_: unknown, r: RecycleCandidateRow) => (
                        <span style={{ display: 'inline-flex', gap: 10 }}>
                          <a onClick={() => decide('approve', r)}>批准回收</a>
                          <a onClick={() => decide('defer', r)}>暂缓</a>
                          <a
                            style={{ color: 'var(--crm-danger, #d45)' }}
                            onClick={() => decide('reject', r)}
                          >
                            驳回
                          </a>
                        </span>
                      ),
                    },
                  ]}
                />
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>自动任务规则</div>
                <Table<TaskRuleRow>
                  columns={[
                    { title: '规则', dataIndex: 'name', width: 220 },
                    {
                      title: '触发条件',
                      dataIndex: 'trigger_type',
                      width: 200,
                      render: (v: string) =>
                        ({
                          quote_no_followup: '报价发出后未跟进',
                          customer_silent: '重点客户久未联系',
                          receivable_due: '应收即将到期',
                        })[v] ?? v,
                    },
                    {
                      title: '天数',
                      width: 160,
                      render: (_: unknown, record: TaskRuleRow) => (
                        <Input
                          defaultValue={String(record.trigger_config?.days ?? 3)}
                          style={{ width: 100 }}
                          onBlur={(event) => {
                            const raw = (event.target as HTMLInputElement).value.trim()
                            const next = Number(raw)
                            if (!raw || !Number.isInteger(next) || next < 0) {
                              Toast.warning('自动任务天数须为非负整数')
                              return
                            }
                            if (Number.isFinite(next) && next !== record.trigger_config?.days) {
                              taskRuleMutation.mutate({ id: record.id, days: next })
                            }
                          }}
                        />
                      ),
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 100,
                      render: (v: string) => (v === 'active' ? '启用' : '停用'),
                    },
                  ]}
                  dataSource={taskRulesQuery.data ?? []}
                  loading={taskRulesQuery.isLoading}
                  rowKey="id"
                  pagination={false}
                />
                <div style={{ marginTop: 12 }}>
                  <Button
                    onClick={() => autoTaskMutation.mutate()}
                    loading={autoTaskMutation.isPending}
                  >
                    立即执行一次
                  </Button>
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 12 }}>
                    同一个业务对象不会重复生成任务
                  </span>
                </div>
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>系统配置</div>
                <Table<SystemSettingRow>
                  columns={[
                    { title: '配置项', dataIndex: 'key', width: 240 },
                    { title: '说明', dataIndex: 'description', width: 260, render: (v: string | null) => v ?? '-' },
                    {
                      title: '值',
                      render: (_: unknown, record: SystemSettingRow) => {
                        const raw = record.value ?? {}
                        const first = Object.values(raw)[0]
                        return (
                          <Input
                            defaultValue={first === undefined ? '' : String(first)}
                            style={{ width: 260 }}
                            onBlur={(event) => {
                              const next = (event.target as HTMLInputElement).value
                              if (next === String(first ?? '')) return
                              const key = Object.keys(raw)[0] ?? 'text'
                              settingMutation.mutate({ key: record.key, value: { [key]: next } })
                            }}
                          />
                        )
                      },
                    },
                  ]}
                  dataSource={settingsQuery.data ?? []}
                  loading={settingsQuery.isLoading}
                  rowKey="id"
                  pagination={false}
                />
              </div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* 用户新增/编辑 */}
      <Modal
        title={userEditTarget ? `编辑用户：${userEditTarget.name}` : '新建用户'}
        visible={userModal}
        onCancel={() => setUserModal(false)}
        onOk={() => userSaveMutation.mutate()}
        confirmLoading={userSaveMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>姓名</FormLabel>
            <Input
              value={userForm.name}
              onChange={(value) => setUserForm({ ...userForm, name: value })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              登录名<span className="required-mark" aria-hidden="true">*</span>{userEditTarget ? '（不可修改）' : ''}
            </div>
            <Input
              value={userForm.username}
              disabled={Boolean(userEditTarget)}
              onChange={(value) => setUserForm({ ...userForm, username: value })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              密码 {userEditTarget ? '（留空表示不修改）' : '*'}
            </div>
            <Input
              mode="password"
              value={userForm.password}
              onChange={(value) => setUserForm({ ...userForm, password: value })}
              placeholder="至少 6 位"
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>手机</div>
              <Input
                value={userForm.mobile}
                onChange={(value) => setUserForm({ ...userForm, mobile: value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>邮箱</div>
              <Input
                value={userForm.email}
                onChange={(value) => setUserForm({ ...userForm, email: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>部门</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择部门"
              showClear
              value={userForm.department_id}
              onChange={(value) =>
                setUserForm({ ...userForm, department_id: (value as number) ?? undefined })
              }
              optionList={deptOptions}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              角色（数据范围取所有角色中最大的一个）
            </div>
            <Select
              multiple
              style={{ width: '100%' }}
              placeholder="选择角色"
              value={userForm.role_ids}
              onChange={(value) => setUserForm({ ...userForm, role_ids: (value as number[]) ?? [] })}
              optionList={roleOptions}
            />
          </div>
        </div>
      </Modal>

      {/* 角色新增/编辑 */}
      <Modal
        title={roleEditTarget ? `编辑角色：${roleEditTarget.name}` : '新建角色'}
        visible={roleModal}
        onCancel={() => setRoleModal(false)}
        onOk={() => roleSaveMutation.mutate()}
        confirmLoading={roleSaveMutation.isPending}
        okText="保存"
        width={620}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                角色编码<span className="required-mark" aria-hidden="true">*</span>{roleEditTarget ? '（不可修改）' : ''}
              </div>
              <Input
                value={roleForm.code}
                disabled={Boolean(roleEditTarget)}
                onChange={(value) => setRoleForm({ ...roleForm, code: value })}
                placeholder="如 salesperson"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>角色名</FormLabel>
              <Input
                value={roleForm.name}
                onChange={(value) => setRoleForm({ ...roleForm, name: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>数据范围</div>
            <Select
              style={{ width: '100%' }}
              value={roleForm.data_scope}
              onChange={(value) => setRoleForm({ ...roleForm, data_scope: value as string })}
              optionList={scopeOptions}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              权限（已选 {roleForm.permission_codes.length} 项）
            </div>
            <Select
              multiple
              filter
              style={{ width: '100%' }}
              placeholder="搜索并选择权限"
              value={roleForm.permission_codes}
              loading={permissionsQuery.isLoading}
              onChange={(value) =>
                setRoleForm({ ...roleForm, permission_codes: (value as string[]) ?? [] })
              }
              optionList={(permissionsQuery.data ?? []).map((perm) => ({
                value: perm.code,
                label: `${perm.name}（${perm.code}）`,
              }))}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>说明</div>
            <Input
              value={roleForm.description}
              onChange={(value) => setRoleForm({ ...roleForm, description: value })}
            />
          </div>
        </div>
      </Modal>

      {/* 部门新增/编辑 */}
      <Modal
        title={deptEditTarget ? `编辑部门：${deptEditTarget.name}` : '新建部门'}
        visible={deptModal}
        onCancel={() => setDeptModal(false)}
        onOk={() => deptSaveMutation.mutate()}
        confirmLoading={deptSaveMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>部门名称</FormLabel>
            <Input
              value={deptForm.name}
              onChange={(value) => setDeptForm({ ...deptForm, name: value })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              上级部门（不选 = 顶级部门）
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择上级部门"
              showClear
              value={deptForm.parent_id}
              onChange={(value) => setDeptForm({ ...deptForm, parent_id: (value as number) ?? undefined })}
              optionList={deptOptions.filter((d) => d.value !== deptEditTarget?.id)}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              不能选自己或自己的下级，否则部门树会成环（后端会拦）。
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              企业微信部门 ID（可选）
            </div>
            <Input
              value={deptForm.wecom_department_id}
              onChange={(value) => setDeptForm({ ...deptForm, wecom_department_id: value })}
            />
          </div>
        </div>
      </Modal>

      {/* 标签编辑 */}
      <Modal
        title="编辑标签"
        visible={tagEditTarget !== null}
        onCancel={() => setTagEditTarget(null)}
        onOk={() => updateTagMutation.mutate()}
        confirmLoading={updateTagMutation.isPending}
        okText="保存"
      >
        {tagEditTarget && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                标签名
              </div>
              <Input
                value={tagEditTarget.name}
                onChange={(value) => setTagEditTarget({ ...tagEditTarget, name: value })}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>分组</div>
              <Select
                style={{ width: '100%' }}
                value={tagEditTarget.type}
                onChange={(value) =>
                  setTagEditTarget({ ...tagEditTarget, type: value as string })
                }
                optionList={[
                  { value: 'custom', label: '自定义' },
                  { value: 'region', label: '区域' },
                  { value: 'industry', label: '行业' },
                  { value: 'grade', label: '等级' },
                  { value: 'channel', label: '渠道' },
                ]}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>状态</div>
              <Select
                style={{ width: '100%' }}
                value={tagEditTarget.status}
                onChange={(value) =>
                  setTagEditTarget({ ...tagEditTarget, status: value as string })
                }
                optionList={[
                  { value: 'active', label: '启用' },
                  { value: 'disabled', label: '停用' },
                ]}
              />
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
