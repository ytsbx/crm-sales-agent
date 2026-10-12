import { useQuery } from '@tanstack/react-query'
import { Empty, Table, Tag } from '@douyinfe/semi-ui'

import { getSendLogs, type QuoteSendLogRow } from '../../shared/api/quote'
import { sendLogsQueryKey } from './sendChannel'

/**
 * 「这份报价发过给谁」——发送记录（主人 2026-10-12 反馈）。
 *
 * ## 为什么补这一块
 *
 * 主人原话：「标记已发送这一块，可模糊，对业务流程不熟悉的话都不知道是发送给客户
 * 还是给自己的 leader」。查下来发现更麻烦的是：**登记完就再也看不到自己登记了什么**。
 *
 * 后端 `GET /quote-versions/{id}/send-logs` 一直都在，还专门做了数据范围校验
 * （注释写着"发送记录里有收件人，不校验的话能直接看到别人把报价发给了谁"），
 * 但**前端一处都没接**。于是"这份报价什么时候、通过什么渠道、发给了谁"
 * 在界面上完全不可核对 —— 而它恰恰是"已发送"这个状态的**依据**。
 *
 * ## 为什么按整单列、不按当前版本列
 *
 * 与合同文档面板同一口径：V1 发给客户、现在走到 V3，那份 V1 的发送事实仍然要看得见
 * （否则"这份报价到底发出去过没有"会被后来的版本覆盖掉）。
 * 后端接口是按**版本**取的，所以这里把整单所有版本的记录合起来按时间排。
 */
export default function QuoteSendLogsPanel({
  versionIds,
  style,
}: {
  /** 这份报价的**所有**版本 id（新→旧都行，这里会自己排）。 */
  versionIds: number[]
  style?: React.CSSProperties
}) {
  // 一个版本一个查询，再合并 —— 后端没有"按报价查发送记录"的接口，
  // 而按版本查正好也复用了它原有的数据范围校验（不用新开口子）。
  const queries = useQuery({
    queryKey: sendLogsQueryKey(versionIds),
    enabled: versionIds.length > 0,
    queryFn: async (): Promise<QuoteSendLogRow[]> => {
      const all = await Promise.all(versionIds.map((id) => getSendLogs(id)))
      return all.flat().sort((a, b) => (a.sent_at < b.sent_at ? 1 : -1))
    },
  })

  const rows = queries.data ?? []

  return (
    <div style={style}>
      <Table<QuoteSendLogRow>
        size="small"
        rowKey="id"
        dataSource={rows}
        loading={queries.isLoading}
        pagination={false}
        empty={
          <Empty
            title="还没有发送记录"
            description="点上面的「标记已发送」，登记这次是发给谁、走的什么渠道。"
          />
        }
        columns={[
          {
            title: '发送时间',
            dataIndex: 'sent_at',
            width: 170,
            render: (value: string) => (value ? value.replace('T', ' ').slice(0, 16) : '-'),
          },
          {
            title: '渠道',
            dataIndex: 'channel',
            width: 110,
            render: (value: string) => <Tag size="small">{value || '-'}</Tag>,
          },
          {
            title: '发给了谁',
            dataIndex: 'receiver',
            render: (value: string | null) =>
              // 老记录可能是空的（那时候收件人不校验，库里真有 `receiver=''` 的行）——
              // 如实说"未登记"，别显示成空白让人以为是加载失败
              value?.trim() ? value : <span style={{ color: 'var(--crm-text-3)' }}>未登记</span>,
          },
        ]}
      />
    </div>
  )
}
