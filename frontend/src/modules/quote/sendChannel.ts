/**
 * 「标记已发送」时用的渠道，以及**按人记住上次用的渠道**（主人 2026-10-12 拍板）。
 *
 * ## 为什么要改
 *
 * 主人原话：「默认方式也不会是邮箱，更多的是企业微信吧，体验很不好」。
 * 原来那个下拉写死 `useState({ channel: '邮件' })` —— 每次打开都回到邮件，
 * 而这个人可能连着 20 单都是走企业微信发的：每单都要重新选一次，
 * **默认值在教用户做错事**。
 *
 * 选的是"让每位用户自己选、系统记住"，而不是直接把默认改成企业微信：
 * 不同岗位的真实渠道不一样（内销走企业微信，外销/大客户走邮件），
 * 一个全局默认值只能对一半人正确。
 *
 * ## 为什么记在浏览器本地，不记到后端
 *
 * 后端**没有**"用户级偏好"这张表 —— `system_settings` 是**全局**的
 * （它带 `updated_by`、每次修改都写审计），把"张三上次用了企业微信"写进去
 * 会污染全局配置，还会在审计里留一堆没有意义的记录。
 * 为这件事新加一张表 + 一套接口，代价明显大于收益：这只是个输入框的默认值，
 * 猜错了用户改一下就行。所以先用 `localStorage`。
 *
 * ⚠️ **按用户 id 分开存**：换账号登录时不能把上一个人的习惯带过来
 * （同一个浏览器上 admin 和业务员看到不同的默认渠道才是对的）。
 * 这也是这份实现里唯一需要小心的地方 —— 所以 key 里必须带用户 id，
 * 拿不到用户 id 时**不记也不读**，宁可用兜底值。
 */

/** 兜底渠道：谁都没选过时用它。取企业微信 —— 主人反馈的实际情况。 */
export const DEFAULT_SEND_CHANNEL = '企业微信'

/**
 * 可选渠道。值是**中文本身**：后端 `quote_send_logs.channel` 是自由文本
 * （`String(32)`，列默认值"手动标记"），所以选中「其他」后用户写的内容
 * 可以直接存回这个字段 —— 见 `otherOption` 的说明，这里不要改成分类码。
 */
export const SEND_CHANNELS = ['企业微信', '微信', '邮件', '其他'] as const

const STORAGE_PREFIX = 'quote-send-channel:'

function storageKey(userId: number | string | null | undefined): string | null {
  if (userId === null || userId === undefined || userId === '') return null
  return `${STORAGE_PREFIX}${userId}`
}

/**
 * 取这个人上次用的渠道；没记过、或记的值已经不在选项里，返回 `DEFAULT_SEND_CHANNEL`。
 *
 * 为什么要校验"还在选项里"：选项表以后可能删掉某一项（比如公司停用某个渠道），
 * 而本地存的是旧值 —— 不校验的话下拉会显示一个列表里没有的项，
 * 用户以为选了其实是个幽灵值。
 */
export function rememberedChannel(userId: number | string | null | undefined): string {
  const key = storageKey(userId)
  if (!key) return DEFAULT_SEND_CHANNEL
  try {
    const saved = window.localStorage.getItem(key)
    if (saved && (SEND_CHANNELS as readonly string[]).includes(saved)) return saved
  } catch {
    // 隐私模式 / 存储被禁：读不到就当没记过，绝不因为"记不住偏好"影响发送
  }
  return DEFAULT_SEND_CHANNEL
}

/** 记住这个人这次用的渠道。写失败就算了 —— 这是偏好，不是业务数据。 */
export function rememberChannel(
  userId: number | string | null | undefined,
  channel: string,
): void {
  const key = storageKey(userId)
  if (!key || !channel) return
  try {
    window.localStorage.setItem(key, channel)
  } catch {
    // 同上：存储写不进去不影响本次发送
  }
}

/**
 * 把选项排成"上次用过的排最前"。
 *
 * 为什么不直接把它设为唯一默认值：真要点开就能看到别的选项，
 * 而**排第一 + 高亮**既表达了默认、又没有把其他选项藏起来。
 * 选中值仍是 `rememberedChannel()` 的返回，这里只管顺序。
 */
export function orderedChannels(userId: number | string | null | undefined): string[] {
  const remembered = rememberedChannel(userId)
  const rest = SEND_CHANNELS.filter((item) => item !== remembered)
  return [remembered, ...rest]
}

/**
 * 发送记录的**查询键**（单一来源）。
 *
 * ⚠️ 必须只有一处定义：我第一版在面板里写 `['quote-send-logs-all', …]`、
 * 在登记成功后的失效里写 `['quote-send-logs', …]` —— 两个键对不上，
 * 于是**登记成功了、下面的"发送记录"还显示"还没有发送记录"**（浏览器实测抓到）。
 * 这类"两处各写一遍"的键，迟早会漂移；抽出来就没有漂移的余地。
 *
 * 按 `版本 id 列表` 拼：这份报价加一版之后列表变了，键自然变，缓存不会串。
 */
export function sendLogsQueryKey(versionIds: number[]): (string | number)[] {
  return ['quote-send-logs', versionIds.join(',')]
}
