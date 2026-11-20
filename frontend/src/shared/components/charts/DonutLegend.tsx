import { colorAt } from './tokens'
import type { NameValue, ValueFormat } from './options'

/**
 * 环形图右侧的图例：**名字 + 数量 + 占比**。
 *
 * 为什么不用 ECharts 自带的图例：它只显示名字，而这张图唯一要说的事就是
 * "各占几成" —— 占比得自己画出来。放在图右侧而不是底部，是为了让卡片长宽比
 * 更接近正方形，一行四张时高矮更好看齐。
 */
export default function DonutLegend({
  data,
  format,
}: {
  data: NameValue[]
  format: ValueFormat
}) {
  const total = data.reduce((sum, item) => sum + item.value, 0)
  /* 超过 6 项就排两列（客户来源 10 项那种）：单列 10 行把卡片拉到 300+ 高，
     同排的「客户等级分布」只有 4 项，于是右边空出两百多像素 —— 实拍就是
     一大片白（2026-11-20 数据分析页）。两列 5 行后高度回到环图那一档，两张卡齐。 */
  const twoCol = data.length > 6
  return (
    /* 图例宽度收到 340 就够（2026-11-19）：
       卡宽 570 时如果让它吃满 400，名字被 flex:1 撑到最左、数字顶到最右，
       一行中间空出两百像素，看着"撒开了"。340 之内名字与数字仍然两端对齐，
       但距离收紧；再长由省略号兜底。 */
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: twoCol ? '1fr 1fr' : undefined,
        columnGap: twoCol ? 16 : undefined,
        rowGap: 7,
        fontSize: 12,
        flex: twoCol ? '0 1 430px' : '0 1 340px',
        maxWidth: twoCol ? 430 : 340,
        minWidth: 0,
      }}
    >
      {data.map((item, index) => (
        <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span
            style={{
              width: 10,
              height: 10,
              flex: 'none',
              borderRadius: 2,
              background: colorAt(index),
            }}
          />
          <span
            title={item.name}
            style={{
              /* 名字按内容宽、不撑满：flex:1 会把名字拉到最左、数字顶到最右，
                 一行中间空出 160px，像两列硬拆开。按内容宽后色块-名字-数字
                 挨在一起，行尾留白交给容器。 */
              flex: '0 1 auto',
              minWidth: 0,
              color: 'var(--crm-text-2)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </span>
          {/* 数量与占比合成一列（`15 家 · 22%`）：
              原来是「名字 + 数量 + 38px 占比」三列并排，四列卡里图例只剩 120 宽，
              名字被挤到只够两个字 —— 实拍全是「省...」「客户...」「电话...」。
              合并后右列约 85 宽，名字能完整显示。 */}
          <span
            style={{
              flex: 'none',
              color: 'var(--crm-text)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {format(item.value)}
            <span style={{ color: 'var(--crm-text-3)', marginLeft: 6 }}>
              · {total ? Math.round((item.value / total) * 100) : 0}%
            </span>
          </span>
        </div>
      ))}
    </div>
  )
}
