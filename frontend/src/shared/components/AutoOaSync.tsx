import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { syncOaInstance, type OaApprovalInstance } from '../api/dingtalk'

/** Read the open approval from OA; list polling alone only reads CRM's saved snapshot. */
export default function AutoOaSync({ row, invalidateKeys, enabled = true }: { enabled?: boolean; row?: OaApprovalInstance | null; invalidateKeys: QueryKey[] }) {
  const cache = useQueryClient()
  const sync = useQuery({
    queryKey: ['oa-live-sync', row?.id],
    queryFn: async () => {
      const latest = await syncOaInstance(row!.id)
      await Promise.all(invalidateKeys.map(queryKey => cache.invalidateQueries({ queryKey })))
      return latest
    },
    enabled: enabled && row?.provider === 'self_oa' && Boolean(row.instance_id) && row.status === 'pending',
    retry: false,
    refetchOnMount: 'always',
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: 'always',
    refetchInterval: query => query.state.data && query.state.data.status !== 'pending'
      ? false : query.state.status === 'error' ? 30000 : 10000,
  })
  return enabled && sync.isError ? <div role="status" style={{ color: 'var(--crm-text-3)', margin: '8px 0' }}>
    暂未取得最新审批进度，稍后会自动重试，也可点击“立即同步”。
  </div> : null
}
