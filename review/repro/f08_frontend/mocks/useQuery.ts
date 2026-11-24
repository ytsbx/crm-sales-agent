/**
 * 用假的 `useQuery` 替换 @tanstack/react-query：让组件在 SSR 下拿到
 * 「成功 / 失败 / 加载中」三种确定状态，不需要真网络、也不需要浏览器。
 *
 * 场景挂在 globalThis 上，避免 Vite 模块缓存导致 runner 与组件读到两份实例。
 */
type Entry = {
  data?: unknown
  isError?: boolean
  error?: Error
  isLoading?: boolean
  isSuccess?: boolean
}

export function useQuery(options: { queryKey?: unknown; enabled?: boolean }) {
  const scope = globalThis as unknown as { __F08_SCENARIO__?: Record<string, Entry> }
  const key = Array.isArray(options.queryKey) ? String(options.queryKey[0]) : ''
  const entry = scope.__F08_SCENARIO__?.[key] ?? {}
  return {
    data: entry.data,
    error: entry.error,
    isError: entry.isError ?? false,
    isLoading: entry.isLoading ?? false,
    isSuccess: entry.isSuccess ?? false,
    isEnabled: options.enabled !== false,
    refetch: async () => ({ isError: entry.isError ?? false, status: entry.isError ? 'error' : 'success' }),
  }
}
