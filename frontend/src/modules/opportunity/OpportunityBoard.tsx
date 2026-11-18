import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Empty, Tag, Toast } from '@douyinfe/semi-ui'

import {
  changeStage,
  listOpportunities,
  listStages,
  type Opportunity,
} from '../../shared/api/opportunity'
import type { TagTone } from '../../shared/types'
import { formatMoney, toTwoDecimals } from '../../shared/components/money'

/**
 * 商机 Kanban 看板（PRD §9 阶段可视化）。
 *
 * 设计稿里没有这个屏，所以只沿用 code.html 的色板、圆角与描边风格自拟：
 * 一列一个阶段，列头显示条数与金额，卡片显示商机名、客户、金额、负责人、风险。
 *
 * 交互上刻意不引入拖拽库（前端没有 dnd 依赖，加包会让构建体积和风格都失控）：
 * 卡片右下角提供"移到下一阶段 / 选择阶段"，落库走的是与详情页同一个
 * `POST /opportunities/{id}/change-stage`，阶段历史、审批与数据权限判定完全一致。
 */

const RISK_TONE: Record<string, TagTone> = { high: 'red', medium: 'orange', low: 'green' }
const RISK_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }

const money = (value?: number | null) =>
  value ? formatMoney(toTwoDecimals(value), 'CNY') : '-'

interface Props {
  /** 关键字/负责人等筛选由列表页统一控制，看板只负责按阶段分列展示。 */
  keyword: string
  onChanged: () => void
  canManage: boolean
}

export default function OpportunityBoard({ keyword, onChanged, canManage }: Props) {
  const navigate = useNavigate()

  const stagesQuery = useQuery({ queryKey: ['stages'], queryFn: listStages })
  const boardQuery = useQuery({
    queryKey: ['opportunity-board', keyword],
    queryFn: () => listOpportunities({ keyword, status: 'open', page: 1, page_size: 200 }),
  })

  const stages = useMemo(
    () =>
      [...(stagesQuery.data ?? [])]
        .filter((stage) => !stage.is_loss)
        .sort((a, b) => a.sequence - b.sequence),
    [stagesQuery.data],
  )

  const grouped = useMemo(() => {
    const map = new Map<number, Opportunity[]>()
    for (const stage of stages) map.set(stage.id, [])
    for (const row of boardQuery.data?.items ?? []) {
      const bucket = map.get(row.stage_id)
      if (bucket) bucket.push(row)
    }
    return map
  }, [stages, boardQuery.data])

  /* 横向滚动提示：ref 挂在滚动容器上，fadeRight 决定右侧渐隐要不要露。
     首次渲染、窗口缩放、阶段列数变化都要重算 —— 少算一次，遮罩就会一直挂着
     （或者滑不动的时候也挂着，等于骗人"右边还有"）。 */
  const scrollRef = useRef<HTMLDivElement>(null)
  const [fadeRight, setFadeRight] = useState(false)
  const updateFade = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // 留 2px 容差：浏览器算出来的 scrollWidth 常比 clientWidth 多一两个小数
    setFadeRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 2)
  }, [])

  useEffect(() => {
    updateFade()
    const el = scrollRef.current
    if (!el) return undefined
    const observer = new ResizeObserver(updateFade)
    observer.observe(el)
    window.addEventListener('resize', updateFade)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateFade)
    }
  }, [updateFade, boardQuery.data, stages])

  const move = async (row: Opportunity, direction: 1 | -1) => {
    const index = stages.findIndex((stage) => stage.id === row.stage_id)
    // 保留停用阶段列以展示旧商机，推进目标只选择启用中的普通阶段。
    const candidates = direction === 1 ? stages.slice(index + 1) : stages.slice(0, index).reverse()
    const next = candidates.find((stage) => stage.status === 'active' && !stage.is_win && !stage.is_loss)
    if (!next) {
      Toast.warning('这个方向没有可用的普通阶段')
      return
    }
    try {
      await changeStage(row.id, { stage_id: next.id })
      Toast.success(`「${row.title}」已推进到「${next.name}」`)
      onChanged()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '推进失败')
    }
  }

  if (boardQuery.isLoading || stagesQuery.isLoading) {
    return <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>看板加载中…</div>
  }

  if ((boardQuery.data?.items ?? []).length === 0) {
    return <Empty description="没有进行中的商机" style={{ padding: '32px 0' }} />
  }

  return (
    /* 8 列 × 248 = 约 2000px，内容区只有 1070 —— 这屏注定要横向滑。
       问题是滑不滑得动**看不出来**：最右边那一列被从中间切断，
       又没有渐隐提示，人只会以为"卡片被裁了"。
       这里给一个右侧渐隐遮罩，出现与否由"还能不能继续往右滑"决定：
       滑到尽头就收掉，免得最后一张卡永远像被抹了一层灰。 */
    <div style={{ position: 'relative' }}>
      <div
        ref={scrollRef}
        onScroll={updateFade}
        style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 8 }}
      >
      {stages.map((stage) => {
        const rows = grouped.get(stage.id) ?? []
        const amount = rows.reduce((sum, row) => sum + (row.expected_amount ?? 0), 0)
        return (
          <div
            key={stage.id}
            style={{
              flex: '0 0 248px',
              width: 248,
              background: 'var(--crm-surface-low)',
              border: '1px solid var(--crm-surface-high)',
              borderRadius: 'var(--crm-radius)',
              display: 'flex',
              flexDirection: 'column',
              maxHeight: 'calc(100vh - 320px)',
            }}
          >
            <div
              style={{
                padding: '10px 12px',
                borderBottom: '1px solid var(--crm-surface-high)',
                position: 'sticky',
                top: 0,
                background: 'var(--crm-surface-low)',
                borderTopLeftRadius: 'var(--crm-radius)',
                borderTopRightRadius: 'var(--crm-radius)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{stage.name}</span>
                {stage.status !== 'active' && <Tag color="grey" size="small">已停用</Tag>}
                {stage.is_win && (
                  <Tag color="green" size="small">
                    成交
                  </Tag>
                )}
                <span style={{ marginLeft: 'auto', color: 'var(--crm-text-3)', fontSize: 12 }}>
                  {rows.length}
                </span>
              </div>
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 2 }}>
                {money(amount)}
              </div>
            </div>

            <div style={{ padding: 8, display: 'grid', gap: 8, overflowY: 'auto' }}>
              {rows.length === 0 && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 12, padding: '8px 4px' }}>
                  暂无商机
                </div>
              )}
              {rows.map((row) => (
                <div
                  key={row.id}
                  onClick={() => navigate(`/opportunities/${row.id}`)}
                  style={{
                    background: 'var(--crm-surface)',
                    border: '1px solid var(--crm-outline)',
                    borderRadius: 'var(--crm-radius-sm)',
                    padding: 10,
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{row.title}</div>
                  <div
                    style={{
                      color: 'var(--crm-text-3)',
                      fontSize: 12,
                      marginBottom: 6,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {row.customer_name ?? '-'}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {/* 没填预计金额时原来是「money() 的默认值 '-'」——
                        一个蓝色粗体的破折号孤零零占一行，看着像个渲染错误。
                        改成灰色小字「未填金额」：说的是一件事，不是一个符号。 */}
                    {row.expected_amount ? (
                      <span style={{ fontSize: 13, color: 'var(--crm-primary)', fontWeight: 600 }}>
                        {money(row.expected_amount)}
                      </span>
                    ) : (
                      <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>未填金额</span>
                    )}
                    {row.risk_level && (
                      <Tag color={RISK_TONE[row.risk_level] ?? 'grey'} size="small">
                        {RISK_LABEL[row.risk_level] ?? row.risk_level}风险
                      </Tag>
                    )}
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      marginTop: 8,
                      color: 'var(--crm-text-3)',
                      fontSize: 12,
                    }}
                  >
                    <span>{row.owner_name ?? '未分配'}</span>
                    <span style={{ marginLeft: 'auto' }}>{row.expected_close_date ?? ''}</span>
                  </div>
                  {canManage && (
                    <div
                      style={{ display: 'flex', gap: 8, marginTop: 8 }}
                      onClick={(event) => event.stopPropagation()}
                    >
                      {/* 两个动作原来都是默认链接色 —— 一样蓝、一样重，
                          于是在卡片底部看不出"哪个是往前的"。
                          退回是反向/低频动作，降到灰色；推进是这一列唯一该做的事，
                          保留主色并加粗一点。 */}
                      <a
                        style={{ fontSize: 12, color: 'var(--crm-text-3)' }}
                        onClick={() => void move(row, -1)}
                      >
                        ← 退回
                      </a>
                      <a
                        style={{ fontSize: 12, fontWeight: 500 }}
                        onClick={() => void move(row, 1)}
                      >
                        推进 →
                      </a>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      })}
      </div>
      {/* 渐隐遮罩：pointer-events:none，不挡点击，只做"还有内容在右边"的提示 */}
      {fadeRight && (
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            bottom: 8,
            width: 56,
            pointerEvents: 'none',
            background:
              'linear-gradient(to right, rgba(255,255,255,0), var(--crm-surface) 78%)',
          }}
        />
      )}
    </div>
  )
}
