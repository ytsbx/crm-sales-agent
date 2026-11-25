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
  // 它会是 undefined；回退仍生成合法 UUID，兼容后端的 UUID 类型请求键。
  const cryptoObj = globalThis.crypto as Crypto | undefined
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID()
  const bytes = new Uint8Array(16)
  if (cryptoObj?.getRandomValues) cryptoObj.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
