/**
 * 关联合同面板 —— 业务详情页里嵌的一块「这一头签了什么」。
 *
 * 客户、订单、报价三处都要问同一个问题，所以做成共享组件。
 * 三处各写一套的话，「依据怎么显示」「状态怎么配色」这些迟早分叉，
 * 到时候同一个状态在三个页面显示三种样子，对账的人先疯。
 *
 * 两个刻意的取舍：
 * 1. 按 order_id / quote_id 走**服务端筛选**，不是把台账拉回来本地过滤 ——
 *    台账分页之后本地那点数据不完整，第 21 份之后就看不见了，
 *    跟当初后台写死 limit(500) 是同一类错：看着是"没有"，其实是"没查"。
 * 2. 这里只给「下载生成稿」。签署原件要拉详情接口，而绝大多数合同压根没有签署件，
 *    为了少数几份在每个客户页上都多打一次请求不划算 —— 要看签署原件去合同台账的详情。
 */

import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Tag } from '@douyinfe/semi-ui'

import { listContractDocuments, type ContractDocument } from '../api/contract'
import { downloadContractDoc } from '../download-contract'
import { usePermissions } from '../hooks/permissions'
import SectionCard from './SectionCard'

/** 与合同台账同一套配色，避免同一个状态两个页面两种颜色。 */
const STATUS_TONE: Record<string, 'green' | 'grey' | 'red'> = {
  signed: 'green',
  draft: 'grey',
  void: 'red',
}

/** 这份合同是照哪一版报价 / 哪张订单签的。光看单号证明不了金额依据，版本必须带上。 */
function basisOf(doc: ContractDocument): string {
  const header = doc.header_snapshot
  if (doc.quote_version_no) return `${header?.quote_no ?? '报价'} V${doc.quote_version_no}`
  if (header?.order_no) return `订单 ${header.order_no}`
  if (header?.quote_no) return `报价 ${header.quote_no}`
  return '未绑定'
}

interface Props {
  /** 三者给一个，决定按哪个维度筛。给多个时后端取交集。 */
  customerId?: number
  orderId?: number
  quoteId?: number
  title?: string
  /** 一次列几条，多的去台账看 */
  pageSize?: number
  empty?: string
  style?: React.CSSProperties
  /** 是否自带卡片外壳。塞进别的卡片里时传 false，避免卡片套卡片。 */
  boxed?: boolean
}

export default function ContractDocsPanel({
  customerId,
  orderId,
  quoteId,
  title = '合同文档',
  pageSize = 5,
  empty = '还没有挂着这家客户的合同文档',
  style,
  boxed = true,
}: Props) {
  const { can } = usePermissions()
  // 列表接口要的是 order:view：没这个权限的角色不该看见入口（后端也会拦）
  const canView = can('order:view')
  const hasKey = Boolean(customerId || orderId || quoteId)

  const query = useQuery({
    queryKey: ['contract-docs', customerId ?? null, orderId ?? null, quoteId ?? null, pageSize],
    queryFn: () =>
      listContractDocuments({
        customer_id: customerId,
        order_id: orderId,
        quote_id: quoteId,
        page: 1,
        page_size: pageSize,
      }),
    enabled: canView && hasKey,
  })

  if (!canView || !hasKey) return null

  const rows = query.data?.items ?? []
  const total = query.data?.total ?? 0

  let body: ReactNode
  if (query.isLoading) {
    body = <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>读取中…</div>
  } else if (rows.length === 0) {
    body = (
      // 原来是两行灰字 + 卡片底部一大片空白，整张卡看着像「半截」
      // （2026-11-16 客户详情巡检）。合成一行，右侧给一个明确的去处。
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
          /* 空态说明原来用三级灰，在浅灰底上几乎看不见（主人 2026-11-17："太淡了"）。
             二级灰仍属次要文字，但至少能读清。 */
          color: 'var(--crm-text-2)',
          fontSize: 13,
        }}
      >
        <span>
          {empty}。如果合同是从订单发起的、当时没关联报价，按报价筛就看不到。
        </span>
        <Link to="/documents">
          <Button size="small" theme="borderless">
            去合同台账按客户查
          </Button>
        </Link>
      </div>
    )
  } else {
    body = (
      <Fragment>
        {rows.map((doc) => (
          <div
            key={doc.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
              padding: '7px 0',
              borderBottom: '1px solid var(--crm-border, #f0f0f0)',
            }}
          >
            <span style={{ fontWeight: 600 }}>{doc.doc_no}</span>
            <Tag size="small">{doc.doc_type_label}</Tag>
            <Tag size="small" color={STATUS_TONE[doc.status] ?? 'grey'}>
              {doc.status_label}
            </Tag>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>依据：{basisOf(doc)}</span>
            {doc.expiry_date && (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                到期 {doc.expiry_date}
              </span>
            )}
            {doc.parent_doc_no && (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                基于 {doc.parent_doc_no}
              </span>
            )}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 12 }}>
              <button type="button" className="crm-text-action" onClick={() => void downloadContractDoc(doc)}>下载合同原稿</button>
            </span>
          </div>
        ))}
        {total > rows.length && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--crm-text-3)' }}>
            共 {total} 份，这里只列最近 {rows.length} 份 ——{' '}
            <Link to="/documents">去合同台账看全部</Link>
          </div>
        )}
      </Fragment>
    )
  }

  // boxed=false：调用方要把这块塞进它自己的卡片里（比如报价详情的「对外与归档」），
  // 再包一层 SectionCard 就成了卡片套卡片。
  if (!boxed) return <>{body}</>

  return (
    <SectionCard title={title} style={style}>
      {body}
    </SectionCard>
  )
}
