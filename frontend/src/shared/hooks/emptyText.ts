/**
 * 列表空态文案。
 *
 * 请求成功但没数据 → 显示页面的"还没有xx"；
 * 请求本身失败（403 无权限 / 40302 不在数据范围等）→ 显示后端的错误说明，
 * 不能让业务员把"没权限"误解成"还没有数据"。
 *
 * 用法：`empty={emptyText(query, '还没有商机')}`
 */
export function emptyText(query: { error: unknown }, fallback: string): string {
  const err = query.error as { message?: string } | null | undefined
  return err?.message ? err.message : fallback
}
