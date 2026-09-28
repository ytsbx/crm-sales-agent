import { useAuthStore } from '../store/auth'

const DATA_SCOPE_LABEL: Record<string, string> = {
  self: '仅本人',
  department: '本部门',
  department_and_sub: '本部门及下级',
  all: '全部',
}

/** 前端权限判断：仅用于隐藏按钮，真正的拦截在后端。 */
export function usePermissions() {
  const user = useAuthStore((state) => state.user)
  const roles = user?.roles ?? []
  const permissions = user?.permissions ?? []

  return {
    isAdmin: roles.includes('admin'),
    // 案例库审核等"主管专属"动作的前端显隐（真正的拦截在后端角色检查）
    isReviewer: roles.includes('sales_manager') || roles.includes('admin'),
    can: (code: string) => roles.includes('admin') || permissions.includes(code),
    dataScopeLabel: DATA_SCOPE_LABEL[user?.data_scope ?? 'self'] ?? '仅本人',
  }
}
