/**
 * 外部链接的安全校验。**全项目只此一处**（2026-10-14 审查修）。
 *
 * ## 为什么要它
 *
 * 项目里有若干"自由文本填 URL"的入口（最典型的是新品洞察的**参考图**：
 * 一个 TextArea，一行一个地址）。这些值会被原样存库，然后在页面上渲染成
 * `<a href={url}>`。而 **React 不会拦 `href` 的协议** ——
 * 把 `javascript:alert(document.cookie)` 填进去，任何人（含从 CSV/接口导入的数据）
 * 点一下那个链接，脚本就在 **CRM 自己的源**下执行：
 * 能读走登录态、能以受害者身份发请求。
 *
 * 项目此前已经修过一处存储型脚本注入（`faddf9c`），这是同一类：
 * **用户可控的字符串进了 URL 位置**。
 *
 * ## 判据
 *
 * 只放行 `http:` / `https:`。其余一律不放行，包括：
 * - `javascript:` / `data:` / `vbscript:` / `file:`（直达脚本执行或本地文件）
 * - `blob:`（同源 blob 能承载 HTML → 也是脚本面）
 * - 相对地址（`/foo`、`foo.png`）：不是"外部链接"，放在"参考图"这种
 *   由用户手填的字段里没有正当用法，宁可不当链接渲染
 *
 * ## 用法
 *
 * 渲染前判一次，**不合法就不要渲染成 `<a>`**（退化成纯文本或提示"地址无效"），
 * 而不是"渲染了但拦住点击" —— 后者一旦漏改一处就破功。
 */

/** 允许出现在 `href` 里的协议白名单（小写，含冒号）。 */
export const SAFE_URL_PROTOCOLS = ['http:', 'https:'] as const

/**
 * 这个地址能不能安全地放进 `href`。
 *
 * 解析失败（`new URL` 抛异常）也返回 `false` —— 宁可不当链接。
 */
export function isSafeExternalUrl(value: unknown): boolean {
  if (typeof value !== 'string') return false
  const text = value.trim()
  if (!text) return false
  // 先按绝对地址解析：协议必须是白名单里的
  let parsed: URL
  try {
    parsed = new URL(text)
  } catch {
    // 相对地址 / 畸形串：不是"外部链接"，不放行
    return false
  }
  return (SAFE_URL_PROTOCOLS as readonly string[]).includes(parsed.protocol.toLowerCase())
}

/**
 * 从一串候选里挑出可安全渲染的地址（保持原顺序）。
 *
 * 用于"一行一个地址"这种批量字段：合法的照常显示成链接，
 * 不合法的丢掉（调用方可以另外提示"有 N 条地址无效"）。
 */
export function safeExternalUrls(values: readonly unknown[] | null | undefined): string[] {
  if (!Array.isArray(values)) return []
  return values.filter((v): v is string => isSafeExternalUrl(v)).map((v) => v.trim())
}
