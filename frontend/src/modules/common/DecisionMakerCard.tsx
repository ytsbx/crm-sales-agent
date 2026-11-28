import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Tag, Toast } from '@douyinfe/semi-ui'

import MaskedContact from '../../shared/components/MaskedContact'
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
  /**
   * 自带外壳时的上间距。其他页面靠父级 gap 分开，报价详情是直接挂在
   * 两列栅格下面的 —— 不传就是 0，卡片会**贴着**上面那张卡的底边
   * （2026-11-18 主人圈出「这里贴太近了」）。空态那支自己带 16。
   */
  marginTop?: number
  /**
   * 空态降级成一行注脚（不带卡片外壳、不带标题栏）。
   * 用在"客户还没登记联系人"的场景：一张卡只装一句话，会把整块版面撑空，
   * 反复被主人圈"空着"（2026-11-16）。有联系人时照旧渲染卡片。
   */
  footnoteOnEmpty?: boolean
}

export default function DecisionMakerCard({
  customerId,
  title = '核心决策人',
  compact = false,
  boxed = true,
  footnoteOnEmpty = false,
  marginTop = 0,
}: Props) {
  const navigate = useNavigate()
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

  // 空态降级：一行说明 + 去加联系人的入口，不占一整张卡
  if (footnoteOnEmpty && contactsQuery.isSuccess && contacts.length === 0) {
    return (
      /* 收起成一行，但**不能裸着挂在上一张卡下面**（2026-11-17 发现）：
         之前 padding 只有 `0 2px`、也没有外壳，看上去像上一张卡掉出来的一截尾巴，
         文字直接贴着卡边。这里补一条窄卡壳：白底 + 圆角 + 细边 + 上下 14px 内边距，
         和上面卡片同宽、同样 16px 间距 —— 一行也算一张卡，只是个子矮。 */
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          fontSize: 12.5,
          color: 'var(--crm-text-3)',
          marginTop: 16,
          padding: '14px 20px',
          background: 'var(--crm-surface)',
          border: '1px solid var(--crm-border, #e6e8ee)',
          borderRadius: 12,
        }}
      >
        <span style={{ color: 'var(--crm-text-2)', fontWeight: 600 }}>{title}</span>
        <span>该客户还没有登记联系人 — 报价要发给谁、谁能拍板都标不出来。</span>
        {/* 按钮推到最右：这一条只有一句话，原来整条右边空出大半屏。
            现在"说明在左、入口贴右"，和上面主数据警告条同一套排法。 */}
        <Button
          size="small"
          theme="borderless"
          style={{ padding: 0, height: 'auto', fontSize: 12.5, marginLeft: 'auto' }}
          onClick={() => navigate(`/customers/${customerId}`)}
        >
          去客户详情加联系人 →
        </Button>
      </div>
    )
  }

  return (
    <div className={boxed ? 'card-block' : undefined} style={boxed ? { marginTop } : undefined}>
      <div className="toolbar" style={{ marginBottom: 8 }}>
        <div style={{ fontWeight: 600 }}>{title}</div>
        <div style={{ flex: 1 }} />
        {contactsQuery.isSuccess && (
          <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            共 {contacts.length} 位联系人
          </span>
        )}
      </div>

      {contactsQuery.isError ? (
        <div role="alert">
          联系人加载失败。
          <Button size="small" theme="borderless" loading={contactsQuery.isFetching} onClick={() => void contactsQuery.refetch()}>
            重试
          </Button>
        </div>
      ) : contactsQuery.isLoading ? (
        <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
      ) : contacts.length === 0 ? (
        // 不用 <Empty>：它自带一个大图标，150px 高，而这里只有一句话要说 ——
        // 商机详情右栏本来就窄，一块空态把下面全撑开了。
        <div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12.5, lineHeight: 1.7 }}>
            该客户还没有登记联系人。这里只会出现客户单位的人，加上之后就能标出谁拍板。
          </div>
          {/* 空态补一个入口：原来只有一句说明，这张卡比左右两张矮一截，
              底边对齐后下半块全空（2026-11-16）。给个能点的地方，
              空态才不是"死胡同"；点过去就是客户详情的联系人区。 */}
          <Button
            size="small"
            theme="borderless"
            style={{ padding: 0, marginTop: 8, fontSize: 12 }}
            onClick={() => navigate(`/customers/${customerId}`)}
          >
            去客户详情加联系人 →
          </Button>
        </div>
      ) : (
        <>
          {/* 最关键的一位单独放大，设计稿里的「核心决策人」就是这一块 */}
          <div
            style={{
              display: 'flex',
              gap: 12,
              /* 12 → 14/16：截图里姓名、职位、联系方式三层挤成一坨，右边又空着
                 （2026-11-20 主人圈出「这里没排版好」）。卡片内边距给足，
                 三层之间各留 6~8px，读起来才是三段信息而不是一段字。 */
              /* 2026-11-20：14/16 → 12/14。右栏这张卡加三个按钮比左栏八字段高出 50px，
                 商机详情的概览被撑成"左上高、右下低"；卡片自身先瘦一圈。 */
              padding: '12px 14px',
              borderRadius: 'var(--crm-radius)',
              background: 'var(--crm-surface-low)',
              border: '1px solid var(--crm-surface-high)',
            }}
          >
            {/* 头像占位去掉了（2026-11-17）：原来是个 40px 圆形，里面放姓名的第一个字。
                姓名常常以「【全场景演示】」开头，第一个字就是「【」，
                圆形里孤零零一个方括号，比没有还难看。姓名本身就是最好的标识。 */}
            <div style={{ minWidth: 0, flex: 1 }}>
              {/* 姓名 + 标签：gap 6 → 8，并且允许换行 —— 姓名带「【全场景演示】」这类
                  长前缀时，标签会被挤到贴着最后一个字，看着像连在一起（2026-11-20）。 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, lineHeight: 1.4, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {primary.name}
                </span>
                {primary.is_primary && (
                  <Tag color="blue" size="small" style={{ flexShrink: 0 }}>
                    主要联系人
                  </Tag>
                )}
              </div>
              <div style={{ color: 'var(--crm-text-2)', fontSize: 12, marginTop: 4 }}>
                {[primary.title, primary.department].filter(Boolean).join(' · ') || '未填职位'}
              </div>
              {/* 邮箱和电话原来靠一个全角空格分隔，截图上挤成「c***@… 无电话」，
                  分不出是两个字段。改成 flex + 10px 间距 + 一个淡灰「·」分隔。 */}
              <div
                style={{
                  color: 'var(--crm-text-3)',
                  fontSize: 12,
                  marginTop: 6,
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: 10,
                }}
              >
                <MaskedContact value={primary.email} kind="email" empty="无邮箱" />
                <span style={{ color: 'var(--crm-text-4, #c9cdd4)' }}>·</span>
                <MaskedContact value={primary.mobile ?? primary.phone} empty="无电话" />
              </div>
            </div>
          </div>

          {/* 三个按钮原来都是 Semi 默认浅灰底，和全站"白底描边"不是一套，
              还容易读成禁用（2026-11-20）。现在：
              发送邮件 = 主按钮（对外沟通的主动作），企业微信 = 文字按钮（复制账号），
              拨打电话 = 文字按钮（无号码时禁用成灰字）。
              ⚠️ Semi 的 Button 默认 type="primary"，只写 theme="borderless" 仍带浅蓝底，
              必须显式 type="tertiary"（全局 CSS 对 tertiary 做了透明兜底）。 */}
          {!compact && (
            <div style={{ display: 'flex', gap: 4, marginTop: 10, flexWrap: 'wrap' }}>
              <Button
                size="small"
                type="primary"
                theme="outline"
                disabled={!primary.email}
                onClick={() => {
                  window.location.href = `mailto:${primary.email}`
                }}
              >
                发送邮件
              </Button>
              <Button
                size="small"
                type="tertiary"
                theme="borderless"
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
                type="tertiary"
                theme="borderless"
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
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 8, minWidth: 0 }}>
                {contacts.slice(1).map((contact, index) => (
                  <div
                    key={contact.id}
                    /* minWidth:0 + overflow:hidden：不加这两条，名字的 nowrap 会一路撑出去，
                       整行冲出右栏、连页面都跟着横向溢出（2026-11-19 第一版实测）。 */
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      fontSize: 13,
                      minWidth: 0,
                      overflow: 'hidden',
                    }}
                  >
                    {/* 右栏只有 340 宽，三个东西并排时原来 Tag 被压成「技…」、
                        名字被挤成两行（2026-11-19 实拍）。
                        现在 Tag 和号码都不让位，中间的名字自己截断 ——
                        职位和号码都是"看一眼就要用的"，名字截了还能悬停看全。 */}
                    <Tag color={toneFor(index)} size="small" style={{ flexShrink: 0 }}>
                      {contact.title ?? contact.department ?? '联系人'}
                    </Tag>
                    <span
                      title={contact.name}
                      style={{
                        minWidth: 0,
                        flex: 1,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {contact.name}
                    </span>
                    <span style={{ color: 'var(--crm-text-3)', fontSize: 12, flexShrink: 0 }}>
                      <MaskedContact value={contact.mobile ?? contact.email} kind={contact.mobile ? 'phone' : 'email'} />
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
