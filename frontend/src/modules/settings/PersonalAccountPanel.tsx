import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Banner, Button, Input, Toast } from '@douyinfe/semi-ui'
import { updatePersonalAccount } from '../../shared/api/auth'
import { useAuthStore } from '../../shared/store/auth'
import { useTabsStore } from '../../shared/store/tabs'
import FormLabel from '../../shared/components/FormLabel'

export default function PersonalAccountPanel() {
  const user = useAuthStore((state) => state.user)
  const navigate = useNavigate()
  const [username, setUsername] = useState<string | null>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const mutation = useMutation({
    mutationFn: updatePersonalAccount,
    onSuccess: (result) => {
      setCurrentPassword(''); setPassword(''); setConfirmation('')
      if (result.require_relogin) {
        Toast.success('账号已保存，请使用修改后的账号重新登录，也可以继续扫码登录')
        useAuthStore.getState().clear()
        useTabsStore.getState().closeAll()
        navigate('/login', { replace: true })
      } else Toast.info('账号没有变化')
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const save = () => {
    const name = (username ?? user?.username ?? '').trim().toLowerCase()
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(name)) { Toast.warning('登录名须以小写字母开头，只能包含小写字母、数字和下划线，最多64位'); return }
    if (!currentPassword) { Toast.warning('请输入当前密码'); return }
    if (password !== confirmation) { Toast.warning('两次新密码不一致'); return }
    if (password && (password.length < 6 || new TextEncoder().encode(password).length > 72)) { Toast.warning('新密码至少6位，最多72字节'); return }
    mutation.mutate({ username: name, current_password: currentPassword, ...(password ? { new_password: password } : {}) })
  }
  return <div style={{ maxWidth: 560, display: 'grid', gap: 16 }}>
    {user?.initial_password_pending && <Banner type="info" description={<>你的登录名：<strong style={{ overflowWrap: 'anywhere' }}>{user.username}</strong>，初始密码：<strong>123456</strong>。可在下方自行修改。</>} />}
    <div><FormLabel required>登录名</FormLabel><Input aria-label="个人登录名" value={username ?? user?.username ?? ''} onChange={setUsername} disabled={mutation.isPending} /></div>
    <div><FormLabel required>当前密码</FormLabel><Input aria-label="当前密码" mode="password" autoComplete="current-password" value={currentPassword} onChange={setCurrentPassword} disabled={mutation.isPending} /></div>
    <div><div style={{ marginBottom: 6 }}>新密码（留空则不修改）</div><Input aria-label="新密码" mode="password" autoComplete="new-password" value={password} onChange={setPassword} disabled={mutation.isPending} /></div>
    <div><div style={{ marginBottom: 6 }}>确认新密码</div><Input aria-label="确认新密码" mode="password" autoComplete="new-password" value={confirmation} onChange={setConfirmation} disabled={mutation.isPending} /></div>
    <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>修改登录名或密码后需要重新登录，钉钉绑定和角色不受影响。</div>
    <div><Button theme="solid" loading={mutation.isPending} onClick={save}>保存个人账号</Button></div>
  </div>
}
