import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { Button, Toast } from '@douyinfe/semi-ui'
import { syncOaInstance } from '../api/dingtalk'

export default function SyncOaButton({ oaId, invalidateKeys }: { oaId: number; invalidateKeys: QueryKey[] }) {
  const cache = useQueryClient()
  const sync = useMutation({
    mutationFn: () => syncOaInstance(oaId),
    onSuccess: async () => {
      await Promise.all(invalidateKeys.map(key => cache.invalidateQueries({ queryKey: key })))
      Toast.success('已查询钉钉最新记录')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  return <Button size="small" loading={sync.isPending} onClick={() => sync.mutate()}>立即同步</Button>
}
