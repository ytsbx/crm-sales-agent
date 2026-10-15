import { useQuery } from '@tanstack/react-query'
import { Tag } from '@douyinfe/semi-ui'

import SectionCard from '../../shared/components/SectionCard'
import { fetchMyPermissions } from '../../shared/api/auth'
import { useAuthStore } from '../../shared/store/auth'
import {
  PERMISSION_RESOURCE_LABEL,
  PERMISSION_SECTIONS,
  UNGROUPED_TITLE,
} from '../../shared/components/PermissionPicker'

/**
 * 「我的信息」+「我的权限」（只读两块）。
 *
 * 改登录名 / 改密码不在这里 —— 那是 `PersonalAccountPanel` 的事，两个面板
 * 并排挂在「个人账号」这一个页签下。拆开的理由很实在：那块要处理校验、
 * 重登、初始密码提示，跟"看一眼"这两块是完全不同的东西，塞一起会互相绊。
 *
 * 权限的分组与中文名复用权限选择件里那一份（同一份口径，不在这里另抄一遍，
 * 免得以后后端加了模块，两个地方只改了一处）。
 */

/** 角色代号 → 中文名（与顶栏用的是同一份口径）。 */
const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  sales_manager: '销售主管',
  salesperson: '业务员',
  finance: '财务',
}

/** 数据范围 → 人话。写英文枚举给用户看没有意义。 */
const SCOPE_LABEL: Record<string, string> = {
  self: '只看自己的',
  department: '看本部门的',
  department_and_sub: '看本部门及下级的',
  all: '看全公司的',
}

/** `customer:create` → `customer`；认不出的码归到「其他权限」。 */
function resourceOf(code: string): string {
  return code.includes(':') ? code.split(':')[0] : code
}

export default function PersonalProfilePanel() {
  const user = useAuthStore((state) => state.user)

  const permQuery = useQuery({
    queryKey: ['my-permissions'],
    queryFn: fetchMyPermissions,
  })

  // 按业务条线分组：直接看一长串权限码等于没给信息
  const names = permQuery.data?.permission_names ?? {}
  const owned = permQuery.data?.permissions ?? []
  const groups = PERMISSION_SECTIONS.map((section) => ({
    title: section.title,
    codes: owned.filter((code) => section.resources.includes(resourceOf(code))),
  })).filter((group) => group.codes.length > 0)
  const grouped = new Set(groups.flatMap((group) => group.codes))
  const rest = owned.filter((code) => !grouped.has(code))

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <SectionCard title="我的信息">
        <div style={{ display: 'grid', gap: 10, fontSize: 13 }}>
          <Row label="姓名" value={user?.name ?? '—'} />
          <Row label="登录账号" value={user?.username ?? '—'} />
          <Row label="部门" value={user?.department ?? '未分配部门'} />
          <Row
            label="角色"
            value={
              (user?.roles ?? []).map((role) => ROLE_LABEL[role] ?? role).join(' / ') ||
              '无角色'
            }
          />
          <Row
            label="数据范围"
            value={SCOPE_LABEL[user?.data_scope ?? ''] ?? user?.data_scope ?? '—'}
          />
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--crm-text-3)' }}>
          姓名、部门、角色这些由管理员维护，本人改不了；要改请找管理员。
          登录名和密码可以在下面那块自己改。
        </div>
      </SectionCard>

      <SectionCard title="我的权限">
        {permQuery.isLoading && (
          <div style={{ fontSize: 13, color: 'var(--crm-text-3)' }}>加载中…</div>
        )}
        {!permQuery.isLoading && owned.length === 0 && (
          <div style={{ fontSize: 13, color: 'var(--crm-text-3)' }}>
            你还没有任何权限，请联系管理员分配角色。
          </div>
        )}
        <div style={{ display: 'grid', gap: 14 }}>
          {groups.map((group) => (
            <Group
              key={group.title}
              title={group.title}
              codes={group.codes}
              names={names}
            />
          ))}
          {rest.length > 0 && (
            <Group title={UNGROUPED_TITLE} codes={rest} names={names} />
          )}
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--crm-text-3)' }}>
          这里列的是你「实际拥有」的权限。觉得少了或多了，请找管理员调整你的角色。
        </div>
      </SectionCard>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <span style={{ width: 72, color: 'var(--crm-text-3)', flexShrink: 0 }}>{label}</span>
      <span>{value}</span>
    </div>
  )
}

function Group({
  title,
  codes,
  names,
}: {
  title: string
  codes: string[]
  names: Record<string, string>
}) {
  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {codes.map((code) => (
          <Tag key={code} color="blue" type="light" size="small">
            {names[code] ?? PERMISSION_RESOURCE_LABEL[resourceOf(code)] ?? code}
          </Tag>
        ))}
      </div>
    </div>
  )
}
