import { IconInbox } from '@douyinfe/semi-icons'
import { Button } from '@douyinfe/semi-ui'
import type { ReactNode } from 'react'

interface EmptyQuery {
  error: unknown
  isFetching?: boolean
  refetch?: () => Promise<unknown>
}

/**
 * 列表空态。
 *
 * 从前全站空态都是一行灰字（「还没有线索」），既没有落点、也没有下一步 ——
 * 新用户第一次进来看到的就是这些，最像半成品。
 * 统一成「图标 + 一句结论 + 一句怎么开始」，其中 hint 要指向**具体动作**
 * （点哪个按钮），而不是重复页面副标题里已经说过的话。
 */
export default function EmptyState({
  title,
  hint,
  icon,
  action,
}: {
  title: string
  hint?: string
  icon?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden="true">
        {icon ?? <IconInbox />}
      </div>
      <div className="empty-state-title">{title}</div>
      {hint && <div className="empty-state-hint">{hint}</div>}
      {action}
    </div>
  )
}

/**
 * 给 Semi `Table` 的 `empty` 用：查询出错时显示错误原因，否则显示正常空态。
 * 与 `emptyText` 同一套判据（有 message 就是错）。
 *
 * 这是**唯一的实现**：`hooks/emptyText.ts` 把 `emptyText` 转发到这里
 * （那个文件保持 `.ts` 扩展名不动，避免 Vite 模块图失效）。
 */
export function emptyTextNode(
  query: EmptyQuery,
  fallback: string,
  hint?: string,
  action?: ReactNode,
) {
  const err = query.error as { message?: string; status?: number } | null | undefined
  if (err) {
    const retry = query.refetch && err.status !== 401 && err.status !== 403 && err.status !== 404
      ? <Button size="small" theme="outline" loading={query.isFetching}
          onClick={() => void query.refetch?.()}>重新加载</Button>
      : undefined
    return <EmptyState title="列表加载失败" hint={err.message || '请稍后再试。'} action={retry} />
  }
  return <EmptyState title={fallback} hint={hint} action={action} />
}

/** 选项式写法（`emptyNode(query, { title, hint })`）：内部就是上面那个函数。 */
export function emptyNode(
  query: EmptyQuery,
  options: { title: string; hint?: string; action?: ReactNode },
) {
  return emptyTextNode(query, options.title, options.hint, options.action)
}
