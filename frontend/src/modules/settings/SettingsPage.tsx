import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Table, Tabs, Tag } from '@douyinfe/semi-ui'

import { listDepartments, listRoles, listUsers } from '../../shared/api/system'
import { listAuditLogs, type AuditLogRow } from '../../shared/api/analytics'
import {
  listPublicPoolRules,
  listSettings,
  listTaskRules,
  runAutoTaskRules,
  runPublicPoolRecycle,
  saveSetting,
  updatePublicPoolRule,
  updateTaskRule,
  type PublicPoolRuleRow,
  type SystemSettingRow,
  type TaskRuleRow,
} from '../../shared/api/settings'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Switch, Toast } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import type { SystemDepartment, SystemRole, SystemUser } from '../../shared/api/system'

const TABS = [
  { tab: '用户', itemKey: 'users' },
  { tab: '角色与数据范围', itemKey: 'roles' },
  { tab: '部门', itemKey: 'departments' },
  { tab: '审计日志', itemKey: 'audit' },
  { tab: '业务规则', itemKey: 'rules' },
]

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
      Toast.success(`已执行，回收 ${data.released_count} 个客户`)
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const autoTaskMutation = useMutation({
    mutationFn: runAutoTaskRules,
    onSuccess: (data) => {
      Toast.success(`已执行，生成 ${data.created_count} 条自动任务`)
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  if (!isAdmin) {
    return (
      <div className="page-container">
        <h2 className="page-title">系统设置</h2>
        <div className="placeholder-box">只有管理员可以查看用户、角色和部门配置。</div>
      </div>
    )
  }

  const userColumns = [
    { title: '姓名', dataIndex: 'name', width: 140 },
    { title: '登录名', dataIndex: 'username', width: 160 },
    { title: '部门', dataIndex: 'department', width: 140, render: (v: string | null) => v ?? '-' },
    { title: '手机', dataIndex: 'mobile', width: 150, render: (v: string | null) => v ?? '-' },
    { title: '邮箱', dataIndex: 'email', render: (v: string | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => (v === 'active' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
  ]

  const roleColumns = [
    { title: '角色码', dataIndex: 'code', width: 160 },
    { title: '角色名', dataIndex: 'name', width: 140 },
    {
      title: '数据范围',
      dataIndex: 'data_scope',
      width: 160,
      render: (v: string) =>
        ({
          self: '仅本人',
          department: '本部门',
          department_and_sub: '本部门及下级',
          all: '全部',
        })[v] ?? v,
    },
    { title: '说明', dataIndex: 'description', render: (v: string | null) => v ?? '-' },
  ]

  const deptColumns = [
    { title: '部门名称', dataIndex: 'name' },
    { title: '上级部门 ID', dataIndex: 'parent_id', width: 140, render: (v: number | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (v: string) => (v === 'active' ? '启用' : '停用'),
    },
  ]

  return (
    <div className="page-container">
      <h2 className="page-title">系统设置</h2>
      <p className="page-subtitle">
        用户与角色目前只读；业务规则、自动任务与系统配置可直接修改
      </p>
      <div className="card-block">
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
            <Table<SystemUser>
              columns={userColumns}
              dataSource={usersQuery.data?.items ?? []}
              loading={usersQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}
          {activeKey === 'roles' && (
            <Table<SystemRole>
              columns={roleColumns}
              dataSource={rolesQuery.data ?? []}
              loading={rolesQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}
          {activeKey === 'departments' && (
            <Table<SystemDepartment>
              columns={deptColumns}
              dataSource={deptsQuery.data ?? []}
              loading={deptsQuery.isLoading}
              rowKey="id"
              pagination={false}
            />
          )}
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
                  客户超过设定天数没有跟进记录，就会被自动释放回公海（原负责人写入历史）
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
                            const next = Number((event.target as HTMLInputElement).value)
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
                    立即执行一次
                  </Button>
                  <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 12 }}>
                    正式运行应由定时任务触发，这里用于验证规则
                  </span>
                </div>
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
                            const next = Number((event.target as HTMLInputElement).value)
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
      </div>
    </div>
  )
}
