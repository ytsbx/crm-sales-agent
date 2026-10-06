/**
 * 对外单据面板（打样需求单 / 下单文件）——文档 §3.5、场景12。
 *
 * 四件事一次说清，避免业务误用：
 * - **生成**：按当前业务资料出图，来源单据（询价/报价）与本次差异一起落快照；
 * - **不覆盖**：再生成是新增一版（V1、V2…），旧版仍在列表里、仍下载得到；
 * - **重试安全**（§8.9）：点"生成一份"时用一把请求键，响应丢了再点还是同一件事
 *   —— 不会多出一份带独立编号的文件；成功之后键作废，下次点才是**明确的"再出一版"**；
 * - **下载读存档原件**（§8.10）：存档的原件永远字节一致（升级出图程序也不变旧件）；
 *   没有存档的历史件拿到的副本纸面上写着"由历史快照重建"；作废件的"已作废"
 *   要看**状态副本**（原件上印的是生成当时的"有效"，它不会跟着状态变）。
 */

import { useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Popconfirm, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { newRequestKey } from '../api/requestKey'
import {
  downloadBizDoc,
  generateOrderDoc,
  generateQuoteDoc,
  generateSampleDoc,
  listBizDocs,
  voidBizDoc,
  type BizDocRow,
} from '../api/bizdoc'

interface Props {
  docType: 'sample_request' | 'order_sheet' | 'quote_sheet'
  /** 打样申请 id（docType=sample_request 时必填） */
  sampleRequestId?: number
  /** 订单 id（docType=order_sheet 时必填） */
  orderId?: number
  /** 报价单 id（docType=quote_sheet 时用于列历史版本） */
  quoteId?: number
  /** 当前选中的报价版本 id（生成时按这一版的快照出图） */
  quoteVersionId?: number
  canManage: boolean
}

const SOURCE_LABEL: Record<string, string> = {
  inquiry: '来源询价',
  quote: '来源报价',
}

export default function BizDocPanel({
  docType,
  sampleRequestId,
  orderId,
  quoteId,
  quoteVersionId,
  canManage,
}: Props) {
  const queryClient = useQueryClient()
  const queryKey = ['biz-docs', docType, sampleRequestId ?? orderId ?? quoteId]

  // 一把键对应"这一次生成"。**故意在成功之前一直保留**：弱网下用户看不到响应会
  // 再点一次，那次必须带同一把键才认得出"这是同一件事"；成功后清空，
  // 于是下一次点击必然是新键 —— 这就是"明确生成新版"，不会被内容查重挡掉。
  const requestKeyRef = useRef<string | null>(null)

  const listQuery = useQuery({
    queryKey,
    queryFn: () =>
      listBizDocs(
        docType === 'sample_request'
          ? { doc_type: docType, sample_request_id: sampleRequestId }
          : docType === 'quote_sheet'
            ? { doc_type: docType, quote_id: quoteId }
            : { doc_type: docType, order_id: orderId },
      ),
  })

  const generateMutation = useMutation({
    mutationFn: () => {
      if (!requestKeyRef.current) requestKeyRef.current = newRequestKey()
      const key = requestKeyRef.current
      return docType === 'sample_request'
        ? generateSampleDoc(sampleRequestId as number, undefined, key)
        : docType === 'quote_sheet'
          ? generateQuoteDoc(quoteVersionId as number, undefined, key)
          : generateOrderDoc(orderId as number, undefined, key)
    },
    onSuccess: (doc) => {
      // 成功了就把键作废：再点一次是"再出一版"，不是一个可能的重复提交
      requestKeyRef.current = null
      Toast.success(`已生成 ${doc.doc_no}（V${doc.version}）`)
      void queryClient.invalidateQueries({ queryKey: ['biz-docs'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const voidMutation = useMutation({
    mutationFn: (id: number) => voidBizDoc(id, '业务作废'),
    onSuccess: () => {
      Toast.success('已作废（原件内容保持不变，需要看"已作废"请下载状态副本）')
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
          需要更新请再生成一份，旧版会保留（不覆盖）。下载默认给**生成时存档的原件**，
          重复下载内容一致；标了「无存档原件」的历史件拿到的是按快照重建的副本。
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
            // 存档状态（§8.10）：不标出来，用户会以为"下载"拿到的都是当初发出去的原件
            title: '原件',
            width: 130,
            render: (_: unknown, r: BizDocRow) =>
              r.archive?.status === 'archived' ? (
                <Tag size="small" color="green">
                  存档原件
                </Tag>
              ) : (
                <Tag size="small" color="orange">
                  无存档原件
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
            width: 200,
            render: (_: unknown, r: BizDocRow) => (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <a onClick={() => void downloadBizDoc(r)}>下载原件</a>
                {r.status === 'void' && (
                  // 作废件：原件上印的是生成当时的"有效"。要看到"已作废"必须走状态副本，
                  // 而且那份是**重出的**，不能和原件混为一谈。
                  <a onClick={() => void downloadBizDoc(r, { mode: 'state' })}>状态副本</a>
                )}
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
