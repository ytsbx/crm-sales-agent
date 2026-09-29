/**
 * 对外单据面板（打样需求单 / 下单文件）——文档 §3.5、场景12。
 *
 * 三件事一次说清，避免业务误用：
 * - **生成**：按当前业务资料出图，来源单据（询价/报价）与本次差异一起落快照；
 * - **不覆盖**：再生成是新增一版（V1、V2…），旧版仍在列表里、仍下载得到；
 * - **下载 ≠ 生效**：下载只出文件，作废要在系统里单独登记（内容不动）。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Popconfirm, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  downloadBizDoc,
  generateOrderDoc,
  generateSampleDoc,
  listBizDocs,
  voidBizDoc,
  type BizDocRow,
} from '../api/bizdoc'

interface Props {
  docType: 'sample_request' | 'order_sheet'
  /** 打样申请 id（docType=sample_request 时必填） */
  sampleRequestId?: number
  /** 订单 id（docType=order_sheet 时必填） */
  orderId?: number
  canManage: boolean
}

const SOURCE_LABEL: Record<string, string> = {
  inquiry: '来源询价',
  quote: '来源报价',
}

export default function BizDocPanel({ docType, sampleRequestId, orderId, canManage }: Props) {
  const queryClient = useQueryClient()
  const queryKey = ['biz-docs', docType, sampleRequestId ?? orderId]

  const listQuery = useQuery({
    queryKey,
    queryFn: () =>
      listBizDocs(
        docType === 'sample_request'
          ? { doc_type: docType, sample_request_id: sampleRequestId }
          : { doc_type: docType, order_id: orderId },
      ),
  })

  const generateMutation = useMutation({
    mutationFn: () =>
      docType === 'sample_request'
        ? generateSampleDoc(sampleRequestId as number)
        : generateOrderDoc(orderId as number),
    onSuccess: (doc) => {
      Toast.success(`已生成 ${doc.doc_no}（V${doc.version}）`)
      void queryClient.invalidateQueries({ queryKey: ['biz-docs'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const voidMutation = useMutation({
    mutationFn: (id: number) => voidBizDoc(id, '业务作废'),
    onSuccess: () => {
      Toast.success('已作废（文件内容保持不变）')
      void queryClient.invalidateQueries({ queryKey: ['biz-docs'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 10,
          fontSize: 12,
          color: 'var(--crm-text-3)',
          lineHeight: 1.7,
        }}
      >
        <span style={{ flex: 1 }}>
          正文取生成时的快照：之后改客户资料、价格或交期，已出的文件不会跟着变。
          需要更新请再生成一份，旧版会保留（不覆盖）。
        </span>
        {canManage && (
          <Button theme="solid" loading={generateMutation.isPending} onClick={() => generateMutation.mutate()}>
            生成一份
          </Button>
        )}
      </div>

      <Table<BizDocRow>
        size="small"
        rowKey="id"
        pagination={false}
        loading={listQuery.isLoading}
        dataSource={listQuery.data ?? []}
        empty="还没有生成过"
        columns={[
          { title: '单号', dataIndex: 'doc_no', width: 170 },
          { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => `V${v}` },
          {
            title: '来源',
            width: 190,
            render: (_: unknown, r: BizDocRow) =>
              r.source?.no
                ? `${SOURCE_LABEL[r.source.type ?? ''] ?? '来源'} ${r.source.no}${
                    r.source.version ? `（第 ${r.source.version} 版）` : ''
                  }`
                : '无',
          },
          { title: '明细', dataIndex: 'item_count', width: 70 },
          {
            title: '差异',
            dataIndex: 'diff_count',
            width: 70,
            render: (v: number) =>
              v > 0 ? <Tag size="small" color="orange">{`${v} 处`}</Tag> : '无',
          },
          {
            title: '状态',
            dataIndex: 'status_label',
            width: 90,
            render: (v: string) => (
              <Tag size="small" color={v === '有效' ? 'green' : 'grey'}>
                {v}
              </Tag>
            ),
          },
          {
            title: '生成时间',
            dataIndex: 'created_at',
            width: 170,
            render: (v: string | null) => (v ? v.slice(0, 19).replace('T', ' ') : '-'),
          },
          {
            title: '操作',
            width: 140,
            render: (_: unknown, r: BizDocRow) => (
              <div style={{ display: 'flex', gap: 10 }}>
                <a onClick={() => void downloadBizDoc(r)}>下载</a>
                {canManage && r.status === 'active' && (
                  <Popconfirm title="作废这份文件？（内容与校验值不变）" onConfirm={() => voidMutation.mutate(r.id)}>
                    <a>作废</a>
                  </Popconfirm>
                )}
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}
