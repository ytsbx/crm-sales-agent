import { useState, type CSSProperties } from 'react'

/* 联系方式默认打码，点一下才展开（2026-11-19 主人："手机号或者较隐私的东西
   默认不打开，点一下再打开"）。

   为什么放在前端做：后端已经在**没有查看权限**时返回星号 + contact_masked，
   那是权限层面的脱敏，改不得。这里管的是另一件事 —— 有权限的人，屏幕上
   也不该一眼把号码全摊开：录屏、截屏、同事路过，都会带走一串真人手机号。
   所以默认只露前 3 位和后 4 位，真要拨号再点开。

   打码规则对齐直觉：手机号 11 位 → 138＊＊＊＊3633；座机/短号这类长度不齐的
   → 留前 3 位和后 2 位；邮箱 → 首字母 + ＊＊＊ + @域名。 */

export function maskPhone(value?: string | null): string {
  const s = String(value ?? '').replace(/\s/g, '')
  if (!s) return ''
  if (s.length >= 11) return `${s.slice(0, 3)}＊＊＊＊${s.slice(-4)}`
  if (s.length > 4) return `${s.slice(0, 3)}＊＊＊${s.slice(-2)}`
  return '＊＊＊'
}

export function maskEmail(value?: string | null): string {
  const s = String(value ?? '').trim()
  if (!s) return ''
  const at = s.indexOf('@')
  if (at <= 0) return `${s.slice(0, 2)}＊＊＊`
  return `${s.slice(0, 1)}＊＊＊${s.slice(at)}`
}

export function maskWechat(value?: string | null): string {
  const s = String(value ?? '').trim()
  if (!s) return ''
  if (s.length <= 3) return '＊＊＊'
  return `${s.slice(0, 2)}＊＊＊${s.slice(-2)}`
}

type MaskedContactProps = {
  value?: string | null
  /** 打码方式；手机号默认 */
  kind?: 'phone' | 'email' | 'wechat'
  /** 后端已脱敏（当前账号没有完整查看权限）：原样显示星号，不给点开 */
  masked?: boolean
  /** 空值显示什么 */
  empty?: string
  style?: CSSProperties
}

export default function MaskedContact({
  value,
  kind = 'phone',
  masked = false,
  empty = '-',
  style,
}: MaskedContactProps) {
  const [revealed, setRevealed] = useState(false)

  if (!value) {
    return <span style={{ color: 'var(--crm-text-3)', ...style }}>{empty}</span>
  }

  // 后端给的就是星号：点开也变不出号码，只留一句说明
  if (masked) {
    return (
      <span
        title="你对这条联系人的联系方式没有完整查看权限"
        style={{ color: 'var(--crm-text-3)', ...style }}
      >
        {value}
      </span>
    )
  }

  const shown = revealed
    ? value
    : kind === 'email'
      ? maskEmail(value)
      : kind === 'wechat'
        ? maskWechat(value)
        : maskPhone(value)

  return (
    <span
      role="button"
      tabIndex={0}
      className={`masked-contact${revealed ? ' is-open' : ''}`}
      title={revealed ? '点击隐藏' : '点击显示完整'}
      style={style}
      onClick={(event) => {
        // 列表行本身可点（跳详情）：这里只切换显示，不能顺带把行点走
        event.stopPropagation()
        setRevealed((v) => !v)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          event.stopPropagation()
          setRevealed((v) => !v)
        }
      }}
    >
      {shown}
    </span>
  )
}
