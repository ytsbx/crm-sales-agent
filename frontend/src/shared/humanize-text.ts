/**
 * 后端文案的「说人话」层（2026-11-17 复验 R-01）。
 *
 * 背景：后端的失败原因里会带内部开关名，比如
 *   推送已临时关闭（WECOM_PUSH_OFF）
 *   钉钉外部调用已关闭（DINGTALK_PUSH_OFF），开发阶段未执行外部请求
 * 这些是给开发和运维看的。销售看到 `WECOM_PUSH_OFF` 只会觉得「系统坏了」，
 * 而不是「企微推送关着，开通后能补投」。所以统一在**展示前**过一层。
 *
 * 注意：必须按**整句**替换。只把括号里的代号换掉会拼出
 * 「推送已临时关闭企业微信推送已关闭，恢复后可补投」这种叠字（第一版踩过）。
 */

/** 按整句替换：左列是后端原文的完整说法，右列是用户能理解的说法。 */
const SENTENCE_RULES: Array<[RegExp, string]> = [
  [
    /推送已临时关闭[（(]\s*WECOM_PUSH_OFF\s*[）)]/g,
    '企业微信推送已关闭，恢复后可补投',
  ],
  [
    /钉钉外部调用已关闭[（(]\s*DINGTALK_PUSH_OFF\s*[）)][，,]?\s*开发阶段未执行外部请求/g,
    '钉钉推送已关闭，恢复后可补投',
  ],
  [/钉钉外部调用已关闭[（(]\s*DINGTALK_PUSH_OFF\s*[）)]/g, '钉钉推送已关闭，恢复后可补投'],
  [/钉钉扫码登录已关闭[（(]\s*DINGTALK_LOGIN_OFF\s*[）)]/g, '钉钉扫码登录暂未开放'],
]

/**
 * 把后端文案转成用户能理解的句子。
 * - `推送已临时关闭（WECOM_PUSH_OFF）` → `企业微信推送已关闭，恢复后可补投`
 * - 其它没登记过的 `（SOME_FLAG）` → 只去掉括号代号，不留英文残渣
 */
export function humanizeBackendText(text?: string | null): string {
  if (!text) return ''
  let result = text
  for (const [pattern, sentence] of SENTENCE_RULES) {
    result = result.replace(pattern, sentence)
  }
  // 兜底：剩下没登记的「全大写 + 下划线」括号注释整段抹掉，
  // 再抹掉裸出现的代号本身（避免 `WECOM_AGENT_ID 未配置` 这种残留）。
  result = result.replace(/[（(]\s*[A-Z][A-Z0-9_]{3,}\s*[）)]/g, '')
  result = result.replace(/\b[A-Z][A-Z0-9_]{3,}\b/g, (code) => KNOWN_BARE[code] ?? '')
  return result.replace(/\s{2,}/g, ' ').replace(/[，,]\s*$/, '').trim()
}

/** 代号裸着出现时（没有括号包裹）用的说法；没登记的留空，直接删掉。 */
const KNOWN_BARE: Record<string, string> = {
  WECOM_PUSH_OFF: '企业微信推送开关',
  DINGTALK_PUSH_OFF: '钉钉推送开关',
  WECOM_AGENT_ID: '企业微信应用 ID',
  DINGTALK_AGENT_ID: '钉钉应用 ID',
  SCHEDULER_ENABLED: '定时任务开关',
}
