/**
 * 「其他」这个兜底选项的通用处理（2026-10-07 主人反馈）。
 *
 * 反馈原话：「给了其他的选项，一般都应该要给写的输入框来写，可以不要求必填，
 * 但应该给填的地方以及保存」。
 *
 * 问题：市场来源、发送方式、费用类型、收款方式、跟进方式这些下拉里都有「其他」，
 * 但选完之后**没有地方写"其他"到底是什么** —— 信息到这一步就丢了，
 * 报表里只能看到一堆"其他"，等于白问。
 *
 * 做法：选中「其他」时露出一个输入框，写的内容**直接存进原字段**
 * （选了"其他"再写"朋友介绍"，字段里就记"朋友介绍"—— 那个内容比"其他"有用得多，
 * 统计时"其他"本来也只是个兜底桶）。**不填就保持「其他」**，不强制必填。
 *
 * 为什么不另开一个"说明"字段：这五个字段后端都是自由文本（不是枚举），
 * 直接写进去就能存下；另开字段要给五张表各加一列 + 五次升级，
 * 换来的只是「其他」和说明分开两列 —— 而这一列本来就不会再拿去做分类。
 * （客户导出用途那处是例外：后端 purpose 是**枚举**且带 purpose_note 校验，
 * 所以它另有一个说明栏，不要照搬。）
 *
 * ## 为什么不需要 useState
 *
 * 「是不是其他」完全能由当前值反推：**值不在"其他之外的预设"里，就是其他**。
 * 于是编辑一条旧记录时，`source='朋友介绍'` 会自动判断成其他、输入框里回填
 * `朋友介绍`，不需要额外的开关状态，也不会出现"开关和值不同步"的经典 bug。
 */

/** 兜底选项的默认文案。 */
export const OTHER_OPTION = '其他'

export interface OtherOptionBinding {
  /** 给 Select 的 `value`：选了「其他」时显示「其他」，否则显示原值。 */
  selectValue: string | undefined
  /** 是否该露出那个输入框。 */
  showInput: boolean
  /** 输入框里显示的内容：值为「其他」时是空的（等用户写），自定义内容时是原值。 */
  inputValue: string
  /** 给 Select 的 `onChange`。 */
  onSelect: (picked: unknown) => void
  /** 给输入框的 `onChange`。 */
  onInput: (text: string) => void
}

/**
 * 把「选项列表 + 当前值 + 写回函数」换算成 Select 与输入框各自要用的东西。
 *
 * 用法：
 * ```tsx
 * const source = otherOption({ options: SOURCES, value: form.source,
 *                              onChange: (v) => setForm({ ...form, source: v }) })
 * <Select value={source.selectValue} onChange={source.onSelect} optionList={...} />
 * {source.showInput && (
 *   <Input value={source.inputValue} onChange={source.onInput} placeholder="请说明具体来源（可不填）" />
 * )}
 * ```
 */
export function otherOption(params: {
  /** 完整选项列表（**含**「其他」本身）。 */
  options: readonly string[]
  /** 当前值；新建未填时给 null / undefined / 空串。 */
  value: string | null | undefined
  /** 值变化时写回去。 */
  onChange: (value: string) => void
  /** 兜底选项文案，默认「其他」。 */
  otherLabel?: string
}): OtherOptionBinding {
  const other = params.otherLabel ?? OTHER_OPTION
  // 「其他」要排除掉：留一个没有「其他」的预设表，
  // 这样「其他」和用户自己写的内容都会落进"不在预设里"这一支，判断只有一条。
  const presets = params.options.filter((item) => item !== other)
  const current = params.value ?? ''
  const isOther = current !== '' && !presets.includes(current)

  return {
    selectValue: isOther ? other : current || undefined,
    showInput: isOther,
    inputValue: current === other ? '' : current,
    onSelect: (picked: unknown) => {
      const next = String(picked ?? '')
      if (next === other) {
        // 切到「其他」时：已经是用户写的自定义内容就**保留**（编辑旧记录不该抹掉），
        // 否则先落成「其他」占位 —— 这样用户不填也有个值，列表里不会是一片空白。
        params.onChange(isOther ? current : other)
      } else {
        params.onChange(next)
      }
    },
    onInput: (text: string) => {
      // 清空（或只留空白）就退回「其他」：既保住"这是个其他类"的信息，
      // 也避免字段里出现一串空格这种脏值。
      params.onChange(text.trim() ? text : other)
    },
  }
}
