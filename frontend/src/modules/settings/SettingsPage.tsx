import { formatBeijingTime } from '../../shared/beijing-time'
import PersonalAccountPanel from './PersonalAccountPanel'
import PersonalProfilePanel from './PersonalProfilePanel'
import UnratedRulesPanel from './UnratedRulesPanel'
import { customerLevelLabel } from '../../shared/customer-level'
import { useState, type CSSProperties } from 'react'
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
import { syncDingTalkUsers, type DingTalkSyncResult, type DingTalkUnmatched } from '../../shared/api/dingtalk'
import PageHeader from '../../shared/components/PageHeader'
import PermissionPicker from '../../shared/components/PermissionPicker'
import {
  batchDecideRecycleCandidates,
  decideRecycleCandidate,
  listPublicPoolRules,
  listDictionaries,
  createDictionary,
  updateDictionary,
  listRecycleCandidates,
  listSettings,
  listTaskRules,
  restoreRecycleCandidate,
  runAutoTaskRules,
  runPublicPoolRecycle,
  saveSetting,
  updatePublicPoolRule,
  updateTaskRule,
  type PublicPoolRuleRow,
  type RecycleCandidateRow,
  type RecycleStatus,
  type SystemSettingRow,
  type TaskRuleRow,
  type DictionaryItemRow,
} from '../../shared/api/settings'
import { Button, Input, Modal, Popconfirm, Select, Switch, Toast } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import type { SystemDepartment, SystemRole, SystemUser } from '../../shared/api/system'
import AuditLogPanel from './AuditLogPanel'
import NotificationSettingsPanel from './NotificationSettingsPanel'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { settingDescription, settingLabel, settingNote } from '../../shared/setting-labels'

const TABS = [
  { tab: '用户', itemKey: 'users' },
  { tab: '角色与数据范围', itemKey: 'roles' },
  { tab: '部门', itemKey: 'departments' },
  { tab: '客户标签', itemKey: 'tags' },
  { tab: '数据字典', itemKey: 'dictionaries' },
  { tab: '通知', itemKey: 'notifications' },
  { tab: '审计日志', itemKey: 'audit' },
  { tab: '业务规则', itemKey: 'rules' },
]

/** 时间显示成"2026-10-06 12:30"（回收复核要看具体哪天）。空值给个短横。 */
function fmtDay(value: string | null | undefined): string {
  return formatBeijingTime(value, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function SettingsPage() {
  // 支持深链：/settings?tab=rules
  const [searchParams, setSearchParams] = useSearchParams()
  const { isAdmin, can } = usePermissions()
  // 个人账号向全部登录人开放；管理与公海复核仍分别按权限开放。
  const canReviewPool = can('customer:pool_review')
  // 第一个页签改叫「我的」而不是「个人账号」：它现在不只有登录名/密码，
  // 还挂着「我的信息」和「我的权限」—— 叫「个人账号」的话，想看自己有哪些
  // 权限的人根本不会往这里点。
  const visibleTabs = [{ tab: '我的', itemKey: 'profile' }, ...(isAdmin ? TABS : canReviewPool ? TABS.filter((item) => item.itemKey === 'rules') : [])]
  const activeKey = visibleTabs.some((item) => item.itemKey === searchParams.get('tab'))
    ? searchParams.get('tab')! : (isAdmin ? 'users' : 'profile')

  // 用户列表：搜索与筛选（后端 `GET /users` 本来就支持 keyword / status /
  // department_id / 分页，从前前端一个都没接 —— 只能靠肉眼在一屏里找人，
  // 而且**超过 50 人后面的人根本显示不出来**（没有翻页，静默截断）。
  //
  // 关键字分成两个状态：`userKeywordInput` 是输入框里的字、`userKeyword`
  // 是**已经提交给服务端**的那个（回车或点「查询」才更新）—— 否则每敲一个字
  // 就发一次请求，人还没打完列表已经翻腾好几轮了。
  const [userKeywordInput, setUserKeywordInput] = useState('')
  const [userKeyword, setUserKeyword] = useState('')
  const [userStatus, setUserStatus] = useState<string>('')
  const [userDeptId, setUserDeptId] = useState<number | undefined>(undefined)
  const [userPage, setUserPage] = useState(1)
  const USER_PAGE_SIZE = 20
  // 任一条件变了都要回到第一页：翻到第 3 页再改条件，"第 3 页"多半已经不存在了
  const resetUserPage = () => setUserPage(1)
  const usersQuery = useQuery({
    queryKey: ['settings-users', userKeyword, userStatus, userDeptId, userPage],
    queryFn: () =>
      listUsers({
        keyword: userKeyword || undefined,
        status: userStatus || undefined,
        department_id: userDeptId,
        page: userPage,
        page_size: USER_PAGE_SIZE,
      }),
    enabled: isAdmin,
  })
  // 角色 / 部门：这两个接口一次就把全部数据返回了（角色就几个、部门通常也就几十个），
  // 后端没有搜索参数 —— 在页面上本地筛就够了，不必为它去改后端。
  const [roleKeyword, setRoleKeyword] = useState('')
  const [deptKeyword, setDeptKeyword] = useState('')
  const rolesQuery = useQuery({ queryKey: ['settings-roles'], queryFn: listRoles, enabled: isAdmin })
  const deptsQuery = useQuery({
    queryKey: ['settings-departments'],
    queryFn: listDepartments,
    enabled: isAdmin,
  })
  const queryClient = useQueryClient()
  const poolRulesQuery = useQuery({
    queryKey: ['public-pool-rules'],
    queryFn: listPublicPoolRules,
    enabled: isAdmin && activeKey === 'rules',
  })
  // 回收候选（预告）：主管逐条复核的对象（返工单 6.3）
  //
  // 返修单第六批第 10 条：此前页面**只查 pending**，于是"暂缓"一点记录就从
  // 页面上消失（其实还在等期满）、已回收的也找不回来（恢复入口无处可点）。
  // 现在按状态分页签：待复核 / 已暂缓 / 已回收 / 已驳回。
  const [candidatePage, setCandidatePage] = useState(1)
  const [candidateStatus, setCandidateStatus] = useState<RecycleStatus>('pending')
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([])
  const candidatesQuery = useQuery({
    queryKey: ['recycle-candidates', candidateStatus, candidatePage],
    queryFn: () =>
      listRecycleCandidates({
        status: candidateStatus,
        page: candidatePage,
        page_size: 20,
      }),
    // 有回收复核权（主管）或管理员都能看，不再限死管理员
    enabled: (isAdmin || canReviewPool) && activeKey === 'rules',
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
  const dictionariesQuery = useQuery({
    queryKey: ['settings-dictionaries'],
    queryFn: () => listDictionaries(undefined, false),
    enabled: isAdmin && activeKey === 'dictionaries',
  })
  const [dictionaryForm, setDictionaryForm] = useState({
    type: 'customer_level', code: '', label: '', sort_no: '0', remark: '',
  })
  // 恢复权限要从权限表里挑，不能让人手敲：敲错一个字母，界面上看起来"设好了"，
  // 实际谁都恢复不了（后端也会拒，但不如直接给选项清楚）
  const recyclePermsQuery = useQuery({
    queryKey: ['settings-permissions'],
    queryFn: listPermissions,
    enabled: isAdmin && activeKey === 'rules',
  })
  const settingRow = (key: string): SystemSettingRow | null =>
    (settingsQuery.data ?? []).find((row) => row.key === key) ?? null
  const noticeSetting = settingRow('pool_recycle_notice_days')
  const deferSetting = settingRow('pool_recycle_defer_days')
  const restoreSetting = settingRow('pool_recycle_restore_permission')

  // 客户标签字典（03-API §7 的 /tags）
  const tagsQuery = useQuery({
    queryKey: ['settings-tags'],
    queryFn: () => listTags(false),
    enabled: isAdmin && activeKey === 'tags',
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
  const dictionaryMutation = useMutation({
    mutationFn: () => createDictionary({
      type: dictionaryForm.type,
      code: dictionaryForm.code.trim(),
      label: dictionaryForm.label.trim(),
      sort_no: Number(dictionaryForm.sort_no) || 0,
      remark: dictionaryForm.remark.trim() || undefined,
    }),
    onSuccess: () => {
      Toast.success('字典项已创建')
      setDictionaryForm((current) => ({ ...current, code: '', label: '', remark: '' }))
      void queryClient.invalidateQueries({ queryKey: ['settings-dictionaries'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const dictionaryUpdateMutation = useMutation({
    mutationFn: ({ id, changes }: { id: number; changes: Parameters<typeof updateDictionary>[1] }) => updateDictionary(id, changes),
    onSuccess: () => {
      Toast.success('字典项状态已保存')
      void queryClient.invalidateQueries({ queryKey: ['settings-dictionaries'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const renderSettingValue = (record: SystemSettingRow) => {
    const raw = record.value ?? {}
    if (record.key === 'approval_levels' && Array.isArray(raw.levels)) {
      // 审批分档改成"就地编辑表格"（2026-10-14 主人拍板）。
      // 旧写法是一张只读小表格 + 一个让你**手写整段 JSON** 的输入框：
      // 同一份数据画两遍，想改还得自己数括号和引号，错一个字符整条不生效，
      // 而且只弹一句提示、写的东西还不退回去。现在表格本身可改。
      //
      // key 挂在数据上：保存成功后 /settings 回来新数据 → 组件重建、显示服务器
      // 真值；用户正在编辑时 props 不变、不重建，免得把没提交的改动冲掉。
      return (
        <ApprovalLevelsEditor
          key={JSON.stringify(raw)}
          levels={raw.levels as Array<Record<string, unknown>>}
          saving={settingMutation.isPending}
          onSave={(levels) => settingMutation.mutate({ key: record.key, value: { levels } })}
        />
      )
    }
    const first = Object.values(raw)[0]
    const key = Object.keys(raw)[0] ?? 'text'
    return (
      <Input
        defaultValue={first === undefined ? '' : String(first)}
        style={{ width: 260 }}
        onBlur={(event) => {
          const next = (event.target as HTMLInputElement).value
          if (next === String(first ?? '')) return
          settingMutation.mutate({ key: record.key, value: { [key]: next } })
        }}
      />
    )
  }

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
    mutationFn: ({ id, decision, note, early }: {
      id: number
      decision: 'approve' | 'reject' | 'defer'
      note?: string
      early?: boolean
    }) => decideRecycleCandidate(id, decision, note, early),
    onSuccess: (row) => {
      Toast.success(`已${row.status_label}`)
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 批量复核结果：**逐条**记住哪条成、哪条为什么没成。
   * 只弹一句"已处理 N 条"是不够的 —— 被拦下的那条还留在待复核里，
   * 页面不列出来，主管会以为都办完了（返修单第六批第 10 条）。
   */
  const [batchResult, setBatchResult] = useState<{
    done: { candidate_id: number; status: string }[]
    failed: { candidate_id: number; reason: string; code: number }[]
  } | null>(null)
  const batchMutation = useMutation({
    mutationFn: (payload: {
      decision: 'approve' | 'reject' | 'defer'
      note?: string
      early?: boolean
    }) =>
      batchDecideRecycleCandidates({ candidateIds: selectedCandidateIds, ...payload }),
    onSuccess: (data) => {
      setBatchResult(data)
      setSelectedCandidateIds([])
      if (data.failed.length) {
        Toast.warning(
          `已处理 ${data.done.length} 条，${data.failed.length} 条未处理（下方逐条列了原因）`,
        )
      } else {
        Toast.success(`已处理 ${data.done.length} 条`)
      }
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: ({ id, note }: { id: number; note?: string }) =>
      restoreRecycleCandidate(id, note),
    onSuccess: () => {
      Toast.success('客户已恢复给原负责人')
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /** 这条到「最早可回收时间」了吗？没到就只能走「提前回收」例外。 */
  const isDue = (row: RecycleCandidateRow) => {
    if (!row.earliest_action_at) return true
    const at = new Date(row.earliest_action_at).getTime()
    return Number.isNaN(at) || Date.now() >= at
  }

  /** 批准/驳回/暂缓。批准和暂缓可能要填说明（尤其"仍有保护但确需回收"时必须填）。 */
  const decide = (
    decision: 'approve' | 'reject' | 'defer',
    row: RecycleCandidateRow,
    options?: { early?: boolean },
  ) => {
    const early = options?.early ?? false
    const label = early
      ? '提前回收'
      : { approve: '批准回收', reject: '驳回', defer: '暂缓' }[decision]
    const protected_ = (row.protection ?? []).length > 0
    const needNote = decision !== 'approve' || protected_ || early
    if (!needNote) {
      decideMutation.mutate({ id: row.id, decision })
      return
    }
    const note = window.prompt(
      early
        ? `提前回收会让预告/暂缓等待期失效（最早可回收时间 ${fmtDay(row.earliest_action_at)}）。`
          + '请填写原因（会记入审计）：'
        : decision === 'approve' && protected_
          ? `该客户仍有履约事项（${(row.protection ?? []).join('；')}）。`
            + '确需例外回收，请填写原因（会记入审计）：'
          : `请填写${label}原因（会记入审计）：`,
    )
    if (note === null) return
    if (!note.trim()) {
      Toast.error(`请填写${label}原因`)
      return
    }
    decideMutation.mutate({ id: row.id, decision, note: note.trim(), early })
  }

  /** 单条恢复：把已回收的客户还给原负责人（已被别人领走时会报冲突）。 */
  const restore = (row: RecycleCandidateRow) => {
    const note = window.prompt('恢复给原负责人，请填写原因（会记入审计）：', '')
    if (note === null) return
    restoreMutation.mutate({ id: row.id, note: note.trim() || undefined })
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
  // 钉钉同步的结果要**逐条看得见**（谁没对上、谁还没绑），所以用弹窗而不是一句话提示：
  // 对不上的人是要管理员去处理的，一闪而过的提示等于没给。
  const [dtSyncVisible, setDtSyncVisible] = useState(false)
  const [dtSyncResult, setDtSyncResult] = useState<DingTalkSyncResult | null>(null)
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
    /** 启用 / 停用。停用后该角色不再参与权限、角色特例与数据范围计算（第十批 10.10）。 */
    status: string
    permission_codes: string[]
  }>({ code: '', name: '', description: '', data_scope: 'self', status: 'active', permission_codes: [] })

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

  // 列表刷新统一走这里：**先取消在途请求再失效**，否则旧请求晚回来会把新数据盖掉。
  const refreshUsers = () => {
    void queryClient.cancelQueries({ queryKey: ['settings-users'] })
    void queryClient.invalidateQueries({ queryKey: ['settings-users'] })
  }
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
        // 用户管理不修改登录名；本人可在个人设置修改。密码留空不重置。
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

  /**
   * 同步钉钉人员：把账号和钉钉通讯录里的人对上。
   *
   * 结果**不看 `disabled` 就当成功**：总闸关着时后端一次请求都没发，
   * 数字全是 0 —— 那和"钉钉里确实没人"是两回事，提示语必须分开。
   */
  const dtSyncMutation = useMutation({
    mutationFn: syncDingTalkUsers,
    onSuccess: (result) => {
      setDtSyncResult(result)
      setDtSyncVisible(true)
      if (!result.disabled) {
        // 绑定了要立刻反映到列表上（刷新走"先取消再失效"，避免拿到在途的旧结果）
        refreshUsers()
      }
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
            status: roleForm.status,
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

  const deptOptions = (deptsQuery.data ?? []).map((dept) => ({
    value: dept.id,
    label: dept.name,
  }))
  const roleOptions = (rolesQuery.data ?? []).map((role) => ({
    value: role.id,
    label: `${role.name}（${role.code}）`,
  }))
  // 角色 / 部门的本地筛选：接口一次返回全部，没必要为它去改后端。
  // 关键字同时匹配"看得见的名"和"机器用的码"—— 管理员手里拿到的可能是
  // `sales_manager` 这种码，也可能是「销售主管」这个名，只匹配一边会搜不到。
  const roleKey = roleKeyword.trim().toLowerCase()
  const filteredRoles = (rolesQuery.data ?? []).filter(
    (role) =>
      !roleKey ||
      role.name.toLowerCase().includes(roleKey) ||
      role.code.toLowerCase().includes(roleKey),
  )
  const deptKey = deptKeyword.trim().toLowerCase()
  const filteredDepts = (deptsQuery.data ?? []).filter(
    (dept) => !deptKey || dept.name.toLowerCase().includes(deptKey),
  )
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
            {(roles ?? []).map((role) => {
              // 停用的角色仍挂在人身上（不删关联），但已经**不生效**：
              // 权限与数据范围都按启用中的角色算。界面上要看得出来，否则
              // 「这个人明明有权限却进不去」会变成排查不了的谜题（第十批 10.10）。
              const disabled = Boolean(role.status) && role.status !== 'active'
              return (
                <Tag key={role.id} size="small" style={disabled ? { opacity: 0.6 } : undefined}>
                  {role.name}
                  {disabled ? '（已停用）' : ''}
                </Tag>
              )
            })}
          </span>
        ),
    },
    { title: '手机', dataIndex: 'mobile', width: 140, render: (v: string | null) => v ?? '-' },
    {
      // 钉钉：**有值才能用钉钉登录**。列上只显示"绑没绑"，编号放进 title 里
      // （管理员平时不需要看那串号，要排查时鼠标一停就能看见）。
      title: '钉钉',
      dataIndex: 'dingtalk_userid',
      width: 110,
      render: (v: string | null) =>
        v ? (
          <span title={v}>
            <Tag color="green">已绑定</Tag>
          </span>
        ) : (
          <span style={{ color: 'var(--crm-text-3)' }}>未绑定</span>
        ),
    },
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
      // 停用状态必须能一眼看到：停用的角色仍然挂在人身上，但已经不参与
      // 权限与数据范围计算（第十批 10.10）。
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
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
                status: record.status ?? 'active',
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
        v ? (deptOptions.find((d) => d.value === v)?.label ?? `部门编号 ${v}`) : '顶级',
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
        // 标题跟着**当前页签**走，不能一律写「系统设置」（返修单 R12 的口径）：
        // 主管只有公海回收复核权，他翻到那一页时标题必须写「公海回收复核」，
        // 否则他会以为自己拿到了整个系统设置、跑去点用户和角色。
        // 翻到「我的」时同理 —— 那是他自己的东西，不叫「系统设置」。
        title={
          isAdmin
            ? '系统设置'
            : activeKey === 'rules'
              ? '公海回收复核'
              : '我的'
        }
        subtitle={
          isAdmin
            ? '用户、角色、部门可在此维护；业务规则、自动任务与系统配置可直接修改'
            : activeKey === 'rules'
              ? '复核本团队客户的回收候选：批准 / 驳回 / 暂缓。看得见哪些候选由你的数据范围决定'
              : '改自己的登录名和密码，看自己的资料与权限'
        }
      />
      <SectionCard>
        <Tabs
          type="line"
          activeKey={activeKey}
          onChange={(key) => {
            setSearchParams({ tab: key })
          }}
          tabList={visibleTabs}
        />
        <div style={{ marginTop: 16 }}>
          {activeKey === 'profile' && (
            <div style={{ display: 'grid', gap: 16 }}>
              {/* 先给"能改的"（登录名 / 密码），再给"只能看的"（资料 / 权限）：
                  进来是想改东西的人不用先滚过一屏只读信息。 */}
              <PersonalAccountPanel />
              <PersonalProfilePanel />
            </div>
          )}
          {activeKey === 'users' && isAdmin && (
            <>
              <div className="toolbar">
                {/* 能搜什么要写在占位符上：不写清楚，等于没做。
                    后端按「姓名或登录名」模糊查（`User.name.ilike | User.username.ilike`）。 */}
                <Input
                  placeholder="搜索姓名 / 登录名"
                  value={userKeywordInput}
                  onChange={setUserKeywordInput}
                  onEnterPress={() => {
                    setUserKeyword(userKeywordInput.trim())
                    resetUserPage()
                  }}
                  style={{ width: 220 }}
                  showClear
                />
                <Button
                  onClick={() => {
                    setUserKeyword(userKeywordInput.trim())
                    resetUserPage()
                  }}
                >
                  查询
                </Button>
                <Select
                  placeholder="全部状态"
                  value={userStatus || undefined}
                  onChange={(value) => {
                    setUserStatus((value as string) ?? '')
                    resetUserPage()
                  }}
                  style={{ width: 120 }}
                  showClear
                  optionList={[
                    { value: 'active', label: '启用' },
                    { value: 'disabled', label: '停用' },
                  ]}
                />
                <Select
                  placeholder="全部部门"
                  value={userDeptId}
                  onChange={(value) => {
                    setUserDeptId(value as number | undefined)
                    resetUserPage()
                  }}
                  style={{ width: 160 }}
                  showClear
                  optionList={deptOptions.map((item) => ({
                    value: item.value as number,
                    label: item.label as string,
                  }))}
                />
                <div style={{ flex: 1 }} />
                <Button
                  onClick={() => dtSyncMutation.mutate()}
                  loading={dtSyncMutation.isPending}
                >
                  同步钉钉人员
                </Button>
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
                empty={
                  userKeyword || userStatus || userDeptId
                    ? '没有符合条件的用户，试试放宽筛选条件'
                    : '还没有用户'
                }
                pagination={{
                  currentPage: userPage,
                  pageSize: USER_PAGE_SIZE,
                  total: usersQuery.data?.total ?? 0,
                  onPageChange: setUserPage,
                }}
              />
            </>
          )}
          {activeKey === 'roles' && (
            <>
              <div className="toolbar">
                {/* 本地筛就够：角色接口一次返回全部，通常也就几个到十几个。 */}
                <Input
                  placeholder="搜索角色码 / 角色名"
                  value={roleKeyword}
                  onChange={setRoleKeyword}
                  style={{ width: 220 }}
                  showClear
                />
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
                      status: 'active',
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
                dataSource={filteredRoles}
                loading={rolesQuery.isLoading}
                rowKey="id"
                empty={roleKeyword ? '没有匹配的角色' : '还没有角色'}
                pagination={false}
              />
            </>
          )}
          {activeKey === 'departments' && (
            <>
              <div className="toolbar">
                {/* 同上：部门接口一次返回全部，本地筛即可。 */}
                <Input
                  placeholder="搜索部门名称"
                  value={deptKeyword}
                  onChange={setDeptKeyword}
                  style={{ width: 220 }}
                  showClear
                />
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
                dataSource={filteredDepts}
                loading={deptsQuery.isLoading}
                rowKey="id"
                empty={deptKeyword ? '没有匹配的部门' : '还没有部门'}
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
          {activeKey === 'dictionaries' && (
            <>
              <div style={{ color: 'var(--crm-text-2)', fontSize: 13, marginBottom: 12 }}>
                客户等级从这里维护；业务表单只允许选择启用的选项，历史数据不会被自动改写。
                产品分类已在新建/编辑产品时直接填写，不在这里维护。
              </div>
              <div className="toolbar">
                <Select
                  style={{ width: 150 }}
                  value={dictionaryForm.type}
                  onChange={(value) => setDictionaryForm({ ...dictionaryForm, type: String(value) })}
                  optionList={[
                    { value: 'customer_level', label: '客户等级' },
                  ]}
                />
                <Input
                  style={{ width: 150 }}
                  placeholder="编码，如 A"
                  value={dictionaryForm.code}
                  onChange={(value) => setDictionaryForm({ ...dictionaryForm, code: value })}
                />
                <Input
                  style={{ width: 180 }}
                  placeholder="显示名称"
                  value={dictionaryForm.label}
                  onChange={(value) => setDictionaryForm({ ...dictionaryForm, label: value })}
                />
                <Input
                  style={{ width: 100 }}
                  placeholder="排序"
                  value={dictionaryForm.sort_no}
                  onChange={(value) => setDictionaryForm({ ...dictionaryForm, sort_no: value })}
                />
                <Button
                  theme="solid"
                  loading={dictionaryMutation.isPending}
                  disabled={!dictionaryForm.code.trim() || !dictionaryForm.label.trim()}
                  onClick={() => dictionaryMutation.mutate()}
                >
                  新建字典项
                </Button>
              </div>
              <Table<DictionaryItemRow>
                columns={[
                  { title: '类型', dataIndex: 'type', width: 140, render: (value: string) => value === 'customer_level' ? '客户等级' : value },
                  { title: '编码', dataIndex: 'code', width: 140 },
                  { title: '名称', dataIndex: 'label' },
                  { title: '排序', dataIndex: 'sort_no', width: 80 },
                  {
                    title: '状态', dataIndex: 'enabled', width: 100,
                    render: (value: boolean) => <Tag color={value ? 'green' : 'grey'}>{value ? '启用' : '停用'}</Tag>,
                  },
                  {
                    title: '操作', width: 120,
                    render: (_: unknown, record: DictionaryItemRow) => (
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        <Button
                          theme="borderless"
                          size="small"
                          loading={dictionaryUpdateMutation.isPending}
                          onClick={() => {
                            const label = window.prompt('修改显示名称', record.label)?.trim()
                            if (label) dictionaryUpdateMutation.mutate({ id: record.id, changes: { label } })
                          }}
                        >
                          编辑
                        </Button>
                        <Button
                          theme="borderless"
                          size="small"
                          loading={dictionaryUpdateMutation.isPending}
                          onClick={() => dictionaryUpdateMutation.mutate({ id: record.id, changes: { enabled: !record.enabled } })}
                        >
                          {record.enabled ? '停用' : '启用'}
                        </Button>
                      </span>
                    ),
                  },
                ]}
                dataSource={dictionariesQuery.data ?? []}
                loading={dictionariesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty="还没有数据字典项"
              />
            </>
          )}
          {activeKey === 'notifications' && <NotificationSettingsPanel />}
          {activeKey === 'audit' && (
            /* 审计日志：搜索 / 筛选 / 分页 + 变更明细（2026-10-15）。
               原来这里是一个写死 `{page:1, page_size:50}`、`pagination={false}` 的表格 ——
               **只能看最近 50 条、没有搜索、没有筛选**，所以主人说"太乱了"。
               后端一直支持那六个筛选参数，是页面没接。 */
            <AuditLogPanel active={activeKey === 'audit'} />
          )}
          {activeKey === 'rules' && (
            <div style={{ display: 'grid', gap: 20 }}>
              {isAdmin && <UnratedRulesPanel />}
              {/* ⚠️ 下面这三块（公海回收规则 / 自动任务规则 / 系统配置）都是**管理员**的活儿：
                  改规则、调参数、跑扫描，后端一律要 `settings:manage`。
                  主管只有 `customer:pool_review`，看到这些控件也点不动（会吃 403），
                  所以对非管理员**整块不渲染** —— 返修单 R12 要求"主管只进入本团队
                  复核功能"，露着不能用的按钮比藏起来更糟。 */}
              {isAdmin && (
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
                    { title: '客户等级', dataIndex: 'level', width: 110, render: (v: string) => customerLevelLabel(v) },
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
                  dataSource={(poolRulesQuery.data ?? []).filter((row) => row.level !== 'UNRATED')}
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
                    正式运行由定时任务触发；这里只「提名」候选，不会直接回收
                  </span>
                </div>
              </div>
              )}

              {isAdmin && (
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>回收节奏与恢复权限</div>
                  <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 12 }}>
                    <b>预告期与暂缓期现在跑的是开发默认值（7 天 / 30 天），还没经过业务确认</b>
                    —— 口径定了在这里改。改完<b>立刻生效</b>：已经提名、还没结案的候选
                    （待复核 / 已暂缓）会按新天数<b>一起重算到期时间</b>，不会新旧两套天数
                    并存；已回收 / 已驳回 / 已作废的不动。三项的改动都会记进审计
                    （含修改前后值、以及同步重算了几条候选）。
                  </div>
                  <div
                    style={{
                      display: 'grid',
                      gap: 16,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
                    }}
                  >
                    <div>
                      <FormLabel>回收预告期（天）</FormLabel>
                      <Input
                        key={`notice-${noticeSetting?.value?.days ?? ''}`}
                        defaultValue={String(noticeSetting?.value?.days ?? 7)}
                        style={{ width: 140 }}
                        onBlur={(event) => {
                          const raw = (event.target as HTMLInputElement).value.trim()
                          const next = Number(raw)
                          if (!raw || !Number.isInteger(next) || next < 0 || next > 365) {
                            Toast.warning('回收预告期须是 0–365 的整数天')
                            return
                          }
                          if (next !== Number(noticeSetting?.value?.days ?? 7)) {
                            settingMutation.mutate({
                              key: 'pool_recycle_notice_days',
                              value: { days: next },
                            })
                          }
                        }}
                      />
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                        扫描命中后先预告这么多天，期间业务员仍可跟进自救；
                        没满之前主管只能走「提前回收」例外。0 = 不设缓冲。
                        {noticeSetting?.is_default ? '（当前为开发默认值）' : ''}
                      </div>
                    </div>
                    <div>
                      <FormLabel>主管暂缓等待期（天）</FormLabel>
                      <Input
                        key={`defer-${deferSetting?.value?.days ?? ''}`}
                        defaultValue={String(deferSetting?.value?.days ?? 30)}
                        style={{ width: 140 }}
                        onBlur={(event) => {
                          const raw = (event.target as HTMLInputElement).value.trim()
                          const next = Number(raw)
                          if (!raw || !Number.isInteger(next) || next < 0 || next > 365) {
                            Toast.warning('主管暂缓等待期须是 0–365 的整数天')
                            return
                          }
                          if (next !== Number(deferSetting?.value?.days ?? 30)) {
                            settingMutation.mutate({
                              key: 'pool_recycle_defer_days',
                              value: { days: next },
                            })
                          }
                        }}
                      />
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                        主管点「暂缓」之后要等这么多天才能再批准回收
                        （暂缓期内随时可以驳回结案）。
                        {deferSetting?.is_default ? '（当前为开发默认值）' : ''}
                      </div>
                    </div>
                    <div>
                      <FormLabel>恢复已回收客户所需权限</FormLabel>
                      <Select
                        value={String(restoreSetting?.value?.text ?? 'customer:assign')}
                        style={{ width: '100%' }}
                        onChange={(value) =>
                          settingMutation.mutate({
                            key: 'pool_recycle_restore_permission',
                            value: { text: String(value) },
                          })
                        }
                        optionList={(recyclePermsQuery.data ?? []).map((item) => ({
                          value: item.code,
                          label: `${item.name}（${item.code}）`,
                        }))}
                      />
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                        默认 <code>customer:assign</code>（指派客户）：销售主管能恢复本团队客户、
                        管理员不受限、普通业务员不能恢复。改这里不影响
                        "谁能看见哪些候选"——那由角色数据范围决定。
                        客户已被别人领取时报冲突，不覆盖。
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>回收复核</div>
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 12 }}>
                  扫描只「提名」；批准后才真的回收，并且<b>必须等预告期/暂缓期满了</b>才能批
                  —— 没满要走「提前回收」这个例外动作，得填原因。
                  「驳回」不受等待期限制（表示不用回收了，随时可结案）。
                  客户手上还有在途订单、未结应收、有效正式报价或在途打样时，批准会被拦下。
                </div>
                <Tabs
                  type="button"
                  activeKey={candidateStatus}
                  onChange={(key) => {
                    setCandidateStatus(key as RecycleStatus)
                    setCandidatePage(1)
                    setSelectedCandidateIds([])
                    setBatchResult(null)
                  }}
                  tabList={[
                    { tab: '待复核', itemKey: 'pending' },
                    { tab: '已暂缓', itemKey: 'deferred' },
                    { tab: '已回收', itemKey: 'executed' },
                    { tab: '已驳回', itemKey: 'rejected' },
                  ]}
                  style={{ marginBottom: 12 }}
                />
                {(candidateStatus === 'pending' || candidateStatus === 'deferred') && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      marginBottom: 8,
                    }}
                  >
                    <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                      已选 {selectedCandidateIds.length} 条
                    </span>
                    <div style={{ flex: 1 }} />
                    {/* 批量决定一次影响多个人/多个客户，点一下立刻生效太轻
                        （主人 2026-10-06 定的范围：批量与不可逆的动作都要确认）。 */}
                    <Popconfirm
                      title={`批准选中的 ${selectedCandidateIds.length} 条回收候选？`}
                      content="批准后进入待执行；到期仍未恢复的客户会被真正回收（归属清空）。"
                      onConfirm={() => batchMutation.mutate({ decision: 'approve' })}
                    >
                      <Button
                        size="small"
                        disabled={!selectedCandidateIds.length}
                        loading={batchMutation.isPending}
                      >
                        批量批准
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title={`暂缓选中的 ${selectedCandidateIds.length} 条回收候选？`}
                      content="暂缓后这些客户这一轮不会被回收，过一阵会重新进入候选。"
                      onConfirm={() => batchMutation.mutate({ decision: 'defer' })}
                    >
                      <Button
                        size="small"
                        disabled={!selectedCandidateIds.length}
                        loading={batchMutation.isPending}
                      >
                        批量暂缓
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title={`驳回选中的 ${selectedCandidateIds.length} 条回收候选？`}
                      content="驳回后本轮不再回收这些客户，需要重新提名才会再进候选。"
                      onConfirm={() => batchMutation.mutate({ decision: 'reject' })}
                    >
                      <Button
                        size="small"
                        type="danger"
                        disabled={!selectedCandidateIds.length}
                        loading={batchMutation.isPending}
                      >
                        批量驳回
                      </Button>
                    </Popconfirm>
                  </div>
                )}
                {batchResult && (
                  <div
                    style={{
                      fontSize: 12,
                      marginBottom: 8,
                      padding: '8px 10px',
                      background: 'var(--crm-surface-low)',
                      borderRadius: 6,
                    }}
                  >
                    <div>成功 {batchResult.done.length} 条；未处理 {batchResult.failed.length} 条。</div>
                    {batchResult.failed.map((row) => (
                      <div key={row.candidate_id} style={{ color: 'var(--crm-danger, #d45)' }}>
                        候选 #{row.candidate_id}：{row.reason}
                      </div>
                    ))}
                  </div>
                )}
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
                  rowSelection={
                    candidateStatus === 'pending' || candidateStatus === 'deferred'
                      ? {
                          selectedRowKeys: selectedCandidateIds,
                          onChange: (keys) => setSelectedCandidateIds((keys ?? []) as number[]),
                        }
                      : undefined
                  }
                  empty={`没有${
                    { pending: '待复核', deferred: '已暂缓', executed: '已回收', rejected: '已驳回' }[
                      candidateStatus
                    ]
                  }的回收候选`}
                  columns={[
                    {
                      title: '客户',
                      dataIndex: 'customer_name',
                      render: (v: string | null, r: RecycleCandidateRow) => (
                        <span>
                          {v ?? `客户编号 ${r.customer_id}`}
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
                      width: 100,
                      render: (v: string | null) => v ?? '-',
                    },
                    {
                      // 复核要看得到"是按哪个时间判它冷落的"，不是只给一句"已超 N 天"
                      title: '最近有效联系',
                      dataIndex: 'last_active_at',
                      width: 140,
                      render: (v: string | null) => fmtDay(v),
                    },
                    ...(candidateStatus === 'executed' || candidateStatus === 'rejected'
                      ? [
                          {
                            title: '处理时间',
                            dataIndex: 'decided_at',
                            width: 140,
                            render: (v: string | null) => fmtDay(v),
                          },
                          {
                            title: '说明',
                            dataIndex: 'decision_note',
                            render: (v: string | null) => v ?? '-',
                          },
                        ]
                      : [
                          {
                            title: '保护事项',
                            dataIndex: 'protection',
                            render: (list: string[], r: RecycleCandidateRow) =>
                              (list ?? []).length === 0 ? (
                                <span style={{ color: 'var(--crm-text-3)' }}>无</span>
                              ) : (
                                <>
                                  {/* 有保护却仍被提名 = 保护已过最长有效期（后端下发标记）。
                                      单独点出来，主管复核时重点看，别当成"没保护"。 */}
                                  {r.protection_overridden && (
                                    <div style={{ marginBottom: 4 }}>
                                      <Tag color="orange" size="small">
                                        有履约事项仍被提名，请重点看
                                      </Tag>
                                    </div>
                                  )}
                                  <span style={{ color: 'var(--crm-caution, #b26a00)' }}>
                                    {(list ?? []).join('；')}
                                  </span>
                                </>
                              ),
                          },
                          {
                            // 到了这个点才能批；没到就只能走「提前回收」例外
                            title: '最早可回收',
                            dataIndex: 'earliest_action_at',
                            width: 140,
                            render: (v: string | null, r: RecycleCandidateRow) =>
                              v ? (
                                <span
                                  style={{
                                    color: isDue(r)
                                      ? 'var(--crm-text-2)'
                                      : 'var(--crm-caution, #b26a00)',
                                  }}
                                >
                                  {fmtDay(v)}
                                  {!isDue(r) && (
                                    <span style={{ fontSize: 11, marginLeft: 4 }}>
                                      （{r.status === 'deferred' ? '暂缓期' : '预告期'}中）
                                    </span>
                                  )}
                                </span>
                              ) : (
                                '-'
                              ),
                          },
                        ]),
                    {
                      title: '操作',
                      width: candidateStatus === 'executed' ? 90 : 230,
                      render: (_: unknown, r: RecycleCandidateRow) => {
                        if (candidateStatus === 'executed') {
                          return (
                            <span>
                              {r.restored_at ? (
                                <Tag color="green" type="light" size="small">
                                  已恢复
                                </Tag>
                              ) : (
                                <a onClick={() => restore(r)}>恢复</a>
                              )}
                            </span>
                          )
                        }
                        if (candidateStatus === 'rejected') {
                          return <span style={{ color: 'var(--crm-text-3)' }}>已结案</span>
                        }
                        return (
                          <span style={{ display: 'inline-flex', gap: 10 }}>
                            {isDue(r) ? (
                              <a onClick={() => decide('approve', r)}>批准回收</a>
                            ) : (
                              <a
                                style={{ color: 'var(--crm-danger, #d45)' }}
                                onClick={() => decide('approve', r, { early: true })}
                              >
                                提前回收
                              </a>
                            )}
                            <a onClick={() => decide('defer', r)}>暂缓</a>
                            <a
                              style={{ color: 'var(--crm-danger, #d45)' }}
                              onClick={() => decide('reject', r)}
                            >
                              驳回
                            </a>
                          </span>
                        )
                      },
                    },
                  ]}
                />
              </div>

              {isAdmin && (
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
                  dataSource={(taskRulesQuery.data ?? []).filter((row) => row.code !== 'customer_unrated_silent')}
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
              )}

              {isAdmin && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>系统配置</div>
                <Table<SystemSettingRow>
                  columns={[
                    {
                      title: '配置项',
                      dataIndex: 'key',
                      width: 240,
                      // 只显示中文名。原来中文名下面还挂着一行灰色小字「内部标识：hard_floor」，
                      // 那是给排查问题/对文档用的开发术语，摆在每一行下面 99% 的时候是噪音。
                      // 主人 2026-10-14 决定删掉；要查标识看《09-业务参数配置说明》。
                      render: (key: string) => <div style={{ fontWeight: 600 }}>{settingLabel(key)}</div>,
                    },
                    {
                      title: '说明',
                      width: 420,
                      render: (_: unknown, record: SystemSettingRow) => {
                        // 一批配置**默认就取最保守的值**（关着 / 0 / 留空），
                        // 因为底价口径、体积重系数、钉钉模板号这些属于业务口径，
                        // 开发不替业务拍板。它们光看说明跟"坏了"一样，
                        // 所以这里单独挂一条"当前为什么看不出效果"（文案单点在
                        // `setting-labels.ts` 的 SETTING_NOTES，只登记这类项）。
                        const note = settingNote(record.key)
                        return (
                          <div>
                            <span>{settingDescription(record.key, record.description)}</span>
                            {note && (
                              <div style={{ marginTop: 4 }}>
                                <Tag color="orange" size="small">
                                  {note.tag}
                                </Tag>
                                <span
                                  style={{
                                    color: 'var(--crm-text-2)',
                                    fontSize: 12,
                                    marginLeft: 6,
                                  }}
                                >
                                  {note.text}
                                </span>
                              </div>
                            )}
                          </div>
                        )
                      },
                    },
                    { title: '值', render: (_: unknown, record: SystemSettingRow) => renderSettingValue(record) },
                  ]}
                  dataSource={settingsQuery.data ?? []}
                  loading={settingsQuery.isLoading}
                  // 用 key 而不是 id 当行标识（2026-10-06 修）：
                  // `/settings` 返回的行里有一批是**代码内置默认项**，库里没有对应
                  // 记录，id 是 null。Semi 对 null 的 rowKey 会**退回数组下标**，
                  // 于是下标 2/10/11/13/14/15 跟同一张表里真实存在的 id 撞车，
                  // 控制台刷 36 条 "two children with the same key"，
                  // 界面上则可能认错行（保存后不刷新/串行）。配置项的 key 唯一且
                  // 必填，保存接口本来就是按 key 走的，用它当行标识天然不会撞。
                  rowKey="key"
                  pagination={false}
                />
              </div>
              )}
            </div>
          )}
        </div>
      </SectionCard>

      {/* 钉钉人员同步的结果：对不上的人必须逐条看得见，否则等于没报 */}
      <Modal
        title="钉钉人员同步"
        visible={dtSyncVisible}
        onCancel={() => setDtSyncVisible(false)}
        footer={
          <Button theme="solid" onClick={() => setDtSyncVisible(false)}>
            知道了
          </Button>
        }
        width={720}
      >
        {dtSyncResult?.disabled ? (
          <>
            <p>本次没有读取钉钉通讯录，一个人都没动。</p>
            <p style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>{dtSyncResult.message}</p>
          </>
        ) : (
          <>
            <p>
              钉钉通讯录共 {dtSyncResult?.total ?? 0} 人：本次绑定 {dtSyncResult?.bound ?? 0} 人，
              之前已绑定 {dtSyncResult?.already_bound ?? 0} 人。
            </p>

            {(dtSyncResult?.unmatched ?? []).length > 0 ? (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 500, marginBottom: 6 }}>
                  没对上的人（{(dtSyncResult?.unmatched ?? []).length} 个，要你去处理）
                </div>
                <Table<DingTalkUnmatched>
                  columns={[
                    { title: '姓名', dataIndex: 'name', width: 120 },
                    { title: '手机', dataIndex: 'mobile', width: 150 },
                    { title: '原因', dataIndex: 'reason_label' },
                  ]}
                  dataSource={dtSyncResult?.unmatched ?? []}
                  rowKey={(row?: DingTalkUnmatched) => `${row?.name ?? ''}|${row?.mobile ?? ''}`}
                  pagination={false}
                  size="small"
                />
              </div>
            ) : (
              <p style={{ color: 'var(--crm-text-3)' }}>钉钉那边的人全都对上了。</p>
            )}

            {(dtSyncResult?.unbound_crm ?? []).length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 500, marginBottom: 6 }}>
                  账号还没绑钉钉的（{(dtSyncResult?.unbound_crm ?? []).length} 个，这些人暂时登不了）
                </div>
                <div style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.8 }}>
                  {(dtSyncResult?.unbound_crm ?? []).map((row) => (
                    <div key={row.id}>
                      {row.name}（{row.username}）
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Modal>

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
              登录名<span className="required-mark" aria-hidden="true">*</span>{userEditTarget ? '（由本人在个人设置修改）' : ''}
            </div>
            <Input
              value={userForm.username}
              disabled={Boolean(userEditTarget)}
              onChange={(value) => setUserForm({ ...userForm, username: value })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              {userEditTarget ? '管理员重置密码（留空不重置）' : '初始密码 *'}
            </div>
            <Input
              mode="password"
              value={userForm.password}
              onChange={(value) => setUserForm({ ...userForm, password: value })}
              placeholder="至少 6 位"
              autoComplete="new-password"
            />
            {userEditTarget && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 6 }}>
                为当前选中的用户设置新密码，无需原密码。修改自己的密码也可从右上角头像进入「个人设置」。
              </div>
            )}
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
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>状态</div>
            <Select
              style={{ width: '100%' }}
              value={roleForm.status}
              onChange={(value) => setRoleForm({ ...roleForm, status: value as string })}
              optionList={[
                { value: 'active', label: '启用' },
                { value: 'disabled', label: '停用（该角色不再参与权限与数据范围）' },
              ]}
            />
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
            <FormLabel style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>权限</FormLabel>
            {/* 按业务条线分组的多选（主人 2026-10-12：「太乱了…要找具体的权限要翻半天」）。
                原来是一个 `Select multiple`，业务员那条角色 23 个标签铺满五行、还得点「∨」
                展开；现在按模块成组、整组可勾、顶部可搜，并且自带滚动不撑长弹窗。 */}
            <PermissionPicker
              loading={permissionsQuery.isLoading}
              value={roleForm.permission_codes}
              onChange={(codes) => setRoleForm({ ...roleForm, permission_codes: codes })}
              options={(permissionsQuery.data ?? []).map((perm) => ({
                code: perm.code,
                name: perm.name,
                // `resource` 在接口里可空；空的归到"其他"，**不丢权限**
                resource: perm.resource ?? 'other',
                action: perm.action ?? '',
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

/**
 * 「报价审批分级」的就地编辑器（2026-10-14 主人拍板，替掉原来那个裸 JSON 输入框）。
 *
 * 后端把这份配置读成**一个数组**，然后从上往下找第一个「金额上限为空 或
 * 报价没超上限」的档（`quote/service.py`）。所以这里有三条不能想当然的规矩：
 *
 * 1. **表格顺序就是判定顺序**，它不是一组可以随便排的无序档位；
 * 2. **「不限」只能出现在最后一档** —— 中间留空一个，它后面的档永远轮不到，
 *    配的时候看不出来，报价一到就全落到中间那一档；
 * 3. 真正决定「谁能批」的只有角色码这一列（`approval/access.py` 拿它跟登录
 *    用户的角色求交集）；`node` 只是写进审批留痕的代号，界面上不给人看 ——
 *    老行沿用原值，新行补一个没被占用的。
 *
 * 另外：**角色留空 ≠ 没人能批，而是「谁有报价审批权限谁都能批」** ——
 * 后端那句判断是「配了角色才校验」，空数组等于不校验。这条得写在界面上，
 * 不然删空一个角色会以为只是"暂时没人管"。
 */
type ApprovalLevelDraft = {
  node: string
  label: string
  /**
   * 金额上限的**原始输入文本**，留空 = 不限。
   * 存文本而不是数字：存数字的话，输入 50000 的过程会被逐字符解析，
   * 5 → 50 → 500 一路跳，光标也不听话。
   */
  maxText: string
  roleCodes: string[]
}

/** 每档里字段名的小字样式。宽度固定，「第 1 档」「第 2 档」的输入框左边才对得齐。 */
const LEVEL_FIELD_LABEL: CSSProperties = {
  width: 56,
  flexShrink: 0,
  fontSize: 12,
  color: 'var(--crm-text-3)',
}

/**
 * 金额上限的**唯一合法写法**：非负数字，最多两位小数。
 *
 * 留空不在这里判 —— 空串另有含义（= 不限），由调用方单独处理。
 *
 * 为什么非要有这条：只判「能不能转成数字」是不够的 —— `Number('1e5')`
 * 就是 100000，是合法数字，一路过关。而用户敲「5万」的时候心里想的是五万，
 * 任何"帮他把非法字符删掉"的做法都只会把值改错（见下面 onChange 的注释）。
 * 所以这里**只认这一种写法**，别的当场拒绝。
 */
const AMOUNT_TEXT_PATTERN = /^\d+(\.\d{1,2})?$/

function toApprovalDrafts(levels: Array<Record<string, unknown>>): ApprovalLevelDraft[] {
  return levels.map((item, index) => ({
    node: String(item.node ?? `level_${index + 1}`),
    label: String(item.label ?? ''),
    maxText: item.max_amount == null ? '' : String(item.max_amount),
    roleCodes: Array.isArray(item.role_codes) ? (item.role_codes as unknown[]).map(String) : [],
  }))
}

function ApprovalLevelsEditor({
  levels,
  saving,
  onSave,
}: {
  levels: Array<Record<string, unknown>>
  saving: boolean
  onSave: (levels: Array<Record<string, unknown>>) => void
}) {
  // 只在挂载时读一次 props：用户编辑期间父组件的 props 不会变（见上面 renderSettingValue
  // 里的 key 说明），所以不需要 effect 去同步 —— 同步反而会把没提交的改动冲掉。
  const [drafts, setDrafts] = useState<ApprovalLevelDraft[]>(() => toApprovalDrafts(levels))
  const rolesQuery = useQuery({ queryKey: ['system-roles'], queryFn: listRoles })

  // 角色下拉的候选：角色表里的中文名，再加上「配置里出现过、角色表里却没有」的
  // 孤儿码 —— 角色被删掉或改了码之后老配置会留下这种，直接吞掉的话用户在下拉里
  // 看不到它、保存时还可能被顺手抹掉。照原样列出来并标注。
  const roleOptions: Array<{ value: string; label: string }> = []
  const seenRoles = new Set<string>()
  for (const role of rolesQuery.data ?? []) {
    if (seenRoles.has(role.code)) continue
    seenRoles.add(role.code)
    roleOptions.push({ value: role.code, label: role.name })
  }
  for (const draft of drafts) {
    for (const code of draft.roleCodes) {
      if (seenRoles.has(code)) continue
      seenRoles.add(code)
      roleOptions.push({ value: code, label: `${code}（角色表里已经没有这个角色）` })
    }
  }

  const patch = (index: number, next: Partial<ApprovalLevelDraft>) =>
    setDrafts((rows) => rows.map((row, i) => (i === index ? { ...row, ...next } : row)))

  const addRow = () => {
    // 新行的内部代号取一个还没被占用的，避免和上面某行撞上（撞了会让留痕对不上）
    const used = new Set(drafts.map((row) => row.node))
    let seq = 1
    while (used.has(`level_${seq}`)) seq += 1
    setDrafts((rows) => [...rows, { node: `level_${seq}`, label: '', maxText: '', roleCodes: [] }])
  }

  const removeRow = (index: number) => setDrafts((rows) => rows.filter((_, i) => i !== index))

  const submit = () => {
    if (drafts.length === 0) {
      Toast.warning('至少要留一档，否则报价提交时找不到该找谁批')
      return
    }
    const next: Array<Record<string, unknown>> = []
    for (let i = 0; i < drafts.length; i += 1) {
      const row = drafts[i]
      const label = row.label.trim()
      if (!label) {
        Toast.warning(`第 ${i + 1} 档还没填级别名称`)
        return
      }
      const text = row.maxText.trim()
      if (text === '') {
        if (i < drafts.length - 1) {
          Toast.warning(`「不限」只能放在最后一档：第 ${i + 1} 档留空之后，后面那些档永远轮不到`)
          return
        }
        next.push({ node: row.node, label, max_amount: null, role_codes: row.roleCodes })
        continue
      }
      // 格式严校。**不能只判「是不是数字」**：`Number('1e5')` 也是合法数字
      // （=100000），照样能混进来；更要紧的是别学以前那样先把非法字符删掉再判 ——
      // 那是把用户填错的值悄悄改成另一个"看起来对"的值，比直接报错危险得多。
      // 拒绝时把原值回显出来，用户才知道是哪一格、填了什么被挡下了。
      if (!AMOUNT_TEXT_PATTERN.test(text)) {
        Toast.warning(
          `第 ${i + 1} 档的「金额上限」填的是「${text}」，不是有效金额：` +
            '请填数字（最多两位小数，例如 50000 或 50000.00）；不限就留空。',
        )
        return
      }
      // 正则已保证是合法数字串，这里不会再是 NaN
      const parsed = Number(text)
      next.push({ node: row.node, label, max_amount: parsed, role_codes: row.roleCodes })
    }
    onSave(next)
  }

  return (
    // width 写死 310，不是 minWidth：这一列是"内容多宽、列就多宽"的自动布局，
    // 内层内容会按自身固有宽度把「值」列往外撑。实测不管住它，「值」列会涨到
    // 462px，加上前两列就把整张配置表顶出内容区、横向滚起来，最右边的操作
    // 直接被推出视口点不到。收在 310 之后这一列约 342px，三列合计约 1002px，
    // 正好落在内容区内（改前是 1052px，要横滚 50px 才看得到右边缘）。
    //
    // 为什么不是一行四列的表格：被前两列挤完之后这一列只剩 340 出头，再扣掉
    // 单元格左右内边距，并排的输入框每个只有 50~60px，连 50000 都显示不全、
    // 「删除」两个字会被压成上下两行。所以每档改成一个小块、字段分两行排。
    <div style={{ display: 'grid', gap: 8, width: 310 }}>
      {drafts.map((row, index) => (
        <div
          key={row.node}
          style={{
            border: '1px solid var(--crm-surface-high)',
            borderRadius: 'var(--crm-radius)',
            padding: '8px 10px',
            display: 'grid',
            gap: 6,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>第 {index + 1} 档</span>
            <a onClick={() => removeRow(index)}>删除</a>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={LEVEL_FIELD_LABEL}>级别</span>
            {/* 级别名是中文字、比数字长，所以宽度按 1.3 : 1 分 —— 平分会把
                「销售主管」裁掉一个字 */}
            <Input
              size="small"
              style={{ flex: 1.3 }}
              value={row.label}
              placeholder="级别名"
              onChange={(value) => patch(index, { label: value })}
            />
            <span style={LEVEL_FIELD_LABEL}>金额上限</span>
            <Input
              size="small"
              style={{ flex: 1 }}
              value={row.maxText}
              placeholder="不限"
              // ⚠️ 原样收下用户敲的每一个字符，**不许在这里删字符**（2026-10-14 修）。
              // 以前写的是 `value.replace(/[^\d.]/g, '')`，本意是防呆，实际是静默篡改：
              // 「5万」被洗成「5」、「1e5」被洗成「15」、「-50000」被洗成「50000」，
              // 洗完全都变成合法数字串，保存时那道校验再也拦不住 —— 用户看到的
              // 和最终存进库的不是一个值，而且**全程没有任何提示**。
              // 现在非法就保持非法、当场标红，由用户自己改对。
              onChange={(value) => patch(index, { maxText: value })}
              validateStatus={
                row.maxText.trim() !== '' && !AMOUNT_TEXT_PATTERN.test(row.maxText.trim())
                  ? 'error'
                  : 'default'
              }
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={LEVEL_FIELD_LABEL}>角色</span>
            <Select
              multiple
              size="small"
              style={{ flex: 1 }}
              value={row.roleCodes}
              placeholder="留空不限"
              maxTagCount={1}
              optionList={roleOptions}
              onChange={(value) =>
                patch(index, { roleCodes: ((value as Array<string | number>) ?? []).map(String) })
              }
            />
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button size="small" onClick={addRow}>
          增加一档
        </Button>
        <Button size="small" theme="solid" loading={saving} onClick={submit}>
          保存
        </Button>
      </div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', lineHeight: 1.6 }}>
        从上往下比金额，所以「不限」只能放最后一档（上限留空即不限）。角色留空不是「没人能批」，
        而是有报价审批权限的人都能批。
      </div>
    </div>
  )
}
