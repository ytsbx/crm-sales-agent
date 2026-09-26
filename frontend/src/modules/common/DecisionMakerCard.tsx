import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Empty, Tag, Toast } from '@douyinfe/semi-ui'

import { listContacts } from '../../shared/api/customer'
import type { Contact } from '../../shared/types'

/**
 * 关键决策人卡片（UI 设计稿：客户详情「核心决策人」/ 商机详情「客户决策关系图」/
 * 报价详情「客户决策关系图」）。
 *
 * 数据全部来自 `contacts` 表，不新增字段：按「主要联系人 → 采购/技术/财务/老板等
 * 决策相关岗位 → 其他」排序，把最能拍板的人放在最上面。
 */

const DECISION_KEYWORDS = ['采购', '技术', '财务', '老板', '总', '经理', '总监', '董事长', '总经理']

function rank(contact: Contact): number {
  if (contact.is_primary) return 0
  const title = `${contact.title ?? ''}${contact.department ?? ''}`
  return DECISION_KEYWORDS.some((word) => title.includes(word)) ? 1 : 2
}

function toneFor(index: number): 'blue' | 'purple' | 'cyan' | 'grey' {
  return (['blue', 'purple', 'cyan', 'grey'] as const)[index % 4]
}

interface Props {
  customerId?: number | null
  /** 卡片标题：客户详情叫「核心决策人」，商机/报价详情叫「客户决策关系图」。 */
  title?: string
  /** 紧凑模式：只列人，不显示快捷动作（用于右栏窄卡片）。 */
  compact?: boolean
  /** 是否自带 .card-block 外壳；嵌在已有卡片里时传 false。 */
  boxed?: boolean
}

export default function DecisionMakerCard({
  customerId,
  title = '核心决策人',
  compact = false,
  boxed = true,
}: Props) {
  const contactsQuery = useQuery({
    queryKey: ['contacts', customerId],
    queryFn: () => listContacts(customerId!),
    enabled: Boolean(customerId),
  })

  const contacts = useMemo(
    () => [...(contactsQuery.data ?? [])].sort((a, b) => rank(a) - rank(b) || a.id - b.id),
    [contactsQuery.data],
  )

  const primary = contacts[0]

  if (!customerId) return null

  return (
    <div className={boxed ? 'card-block' : undefined}>
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <div style={{ fontWeight: 600 }}>{title}</div>
        <div style={{ flex: 1 }} />
        <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
          共 {contacts.length} 位联系人
        </span>
      </div>

      {contactsQuery.isLoading ? (
        <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
      ) : contacts.length === 0 ? (
        <Empty description="该客户还没有联系人" style={{ padding: '12px 0' }} />
      ) : (
        <>
          {/* 最关键的一位单独放大，设计稿里的「核心决策人」就是这一块 */}
          <div
            style={{
              display: 'flex',
              gap: 12,
              padding: 12,
              borderRadius: 'var(--crm-radius)',
              background: 'var(--crm-surface-low)',
              border: '1px solid var(--crm-surface-high)',
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                flex: '0 0 40px',
                borderRadius: '50%',
                background: 'var(--crm-primary-soft)',
                color: 'var(--crm-primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 600,
              }}
            >
              {primary.name.slice(0, 1)}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontWeight: 600 }}>{primary.name}</span>
                {primary.is_primary && (
                  <Tag color="blue" size="small">
                    主要联系人
                  </Tag>
                )}
              </div>
              <div style={{ color: 'var(--crm-text-2)', fontSize: 12, marginTop: 2 }}>
                {[primary.title, primary.department].filter(Boolean).join(' · ') || '未填职位'}
              </div>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                {primary.email ?? '无邮箱'}　{primary.mobile ?? primary.phone ?? '无电话'}
              </div>
            </div>
          </div>

          {!compact && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              <Button
                size="small"
                disabled={!primary.email}
                onClick={() => {
                  window.location.href = `mailto:${primary.email}`
                }}
              >
                发送邮件
              </Button>
              <Button
                size="small"
                onClick={async () => {
                  const account = primary.wechat ?? primary.mobile ?? primary.name
                  try {
                    await navigator.clipboard.writeText(account)
                    Toast.success(`已复制 ${account}，可在企业微信中搜索添加`)
                  } catch {
                    Toast.info(`企业微信账号：${account}`)
                  }
                }}
              >
                企业微信
              </Button>
              <Button
                size="small"
                disabled={!(primary.mobile ?? primary.phone)}
                onClick={() => {
                  window.location.href = `tel:${primary.mobile ?? primary.phone}`
                }}
              >
                拨打电话
              </Button>
            </div>
          )}

          {contacts.length > 1 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 8 }}>
                决策链其他联系人
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {contacts.slice(1).map((contact, index) => (
                  <div
                    key={contact.id}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
                  >
                    <Tag color={toneFor(index)} size="small">
                      {contact.title ?? contact.department ?? '联系人'}
                    </Tag>
                    <span>{contact.name}</span>
                    <span style={{ color: 'var(--crm-text-3)', fontSize: 12, marginLeft: 'auto' }}>
                      {contact.mobile ?? contact.email ?? '-'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
