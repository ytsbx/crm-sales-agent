/**
 * 请求幂等键（第八批 8.15）。
 *
 * 为什么不是"提交时临时生成一个"：幂等键的意义在于**同一份表单的多次提交
 * 用同一把键**。如果在 `onOk` 里现生成，弱网下重试仍然会换一把新键，
 * 服务端照样建第二条 —— 代码看着有幂等，实际没起作用。
 *
 * 用法（一个表单一把键）：
 *   const keyRef = useRef('')
 *   打开表单时：keyRef.current = newRequestKey()
 *   提交时：createXxx({ ...values, request_key: keyRef.current })
 *   成功后清空，下次打开表单再生成新的
 *
 * 键按（用户 + 接口）分区，所以这里的 uuid 不需要全局唯一。
 */
export function newRequestKey(): string {
  // crypto.randomUUID 需要安全上下文（https 或 localhost）。内网 http 部署下
  // 它会是 undefined —— 那时退化成"时间戳 + 随机数"，唯一性够用，
  // 而且绝不能在用户点提交时才报一个 TypeError。
  const cryptoObj = globalThis.crypto as Crypto | undefined
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID()
  return `rk-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}
