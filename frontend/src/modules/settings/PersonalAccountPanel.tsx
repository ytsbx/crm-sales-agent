import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import { Banner, Button, Input, Toast } from '@douyinfe/semi-ui'
import { updatePersonalAccount } from '../../shared/api/auth'
import { loginUrl } from '../../shared/safe-url'
import { currentAuthEpoch, useAuthStore } from '../../shared/store/auth'
import { useTabsStore } from '../../shared/store/tabs'
import FormLabel from '../../shared/components/FormLabel'

export default function PersonalAccountPanel() {
  const user = useAuthStore((state) => state.user)
  const navigate = useNavigate()
  const { pathname, search, hash } = useLocation()
  const [username, setUsername] = useState<string | null>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const mutation = useMutation({
    mutationFn: async (payload: Parameters<typeof updatePersonalAccount>[0]) => {
      const sentAt = currentAuthEpoch()
      const result = await updatePersonalAccount(payload)
      return { result, sentAt }
    },
    onSuccess: ({ result, sentAt }) => {
      if (sentAt !== currentAuthEpoch()) return
      setCurrentPassword('')
      setPassword('')
      setConfirmation('')
      if (result.require_relogin) {
        Toast.success('账号已保存，请重新登录')
        useAuthStore.getState().clear()
        useTabsStore.getState().closeAll()
        navigate(loginUrl(pathname + search + hash), { replace: true })
      } else {
        Toast.info('账号没有变化')
      }
    },
  })

  const name = (username ?? user?.username ?? '').trim().toLowerCase()
  const changing = Boolean(user && (name !== user.username || password || confirmation))
  const invalidName = !/^[a-z][a-z0-9_]{0,63}$/.test(name)
  const invalidPassword = Boolean(password && (password.length < 6 || new TextEncoder().encode(password).length > 72))
  const mismatch = password !== confirmation
  const canSave = changing && !invalidName && !invalidPassword && !mismatch && Boolean(currentPassword)
  const updateField = (setter: (value: string) => void) => (value: string) => {
    if (mutation.isError) mutation.reset()
    setter(value)
  }

  return (
    <form className="settings-account-form" onSubmit={(event) => {
      event.preventDefault()
      if (!canSave || mutation.isPending) return
      mutation.mutate({ username: name, current_password: currentPassword, ...(password ? { new_password: password } : {}) })
    }}>
      {user?.initial_password_pending && (
        <Banner type="info" description={<>你的登录名：<strong style={{ overflowWrap: 'anywhere' }}>{user.username}</strong>，初始密码：<strong>123456</strong>。可在下方自行修改。</>} />
      )}
      <div className="settings-account-fields">
        <div>
          <FormLabel required>登录名</FormLabel>
          <Input aria-label="个人登录名" autoComplete="username" value={username ?? user?.username ?? ''}
            onChange={updateField(setUsername)} disabled={mutation.isPending} aria-invalid={Boolean(username !== null && invalidName)} />
          {username !== null && invalidName && (
            <div className="settings-account-error">请以字母开头，只使用字母、数字和下划线，最多 64 位。</div>
          )}
        </div>
        <div>
          <FormLabel required>当前密码</FormLabel>
          <Input aria-label="当前密码" mode="password" autoComplete="current-password" value={currentPassword}
            onChange={updateField(setCurrentPassword)} disabled={mutation.isPending} />
        </div>
        <div>
          <FormLabel>新密码（选填）</FormLabel>
          <Input aria-label="新密码" mode="password" autoComplete="new-password" value={password}
            onChange={updateField(setPassword)} disabled={mutation.isPending} aria-invalid={invalidPassword} />
          {invalidPassword && <div className="settings-account-error">至少 6 个字符，最多 72 字节。</div>}
        </div>
        <div>
          <FormLabel>确认新密码</FormLabel>
          <Input aria-label="确认新密码" mode="password" autoComplete="new-password" value={confirmation}
            onChange={updateField(setConfirmation)} disabled={mutation.isPending} aria-invalid={Boolean(confirmation && mismatch)} />
          {confirmation && mismatch && <div className="settings-account-error">两次输入的新密码不一致。</div>}
        </div>
      </div>
      {mutation.isError && <div role="alert" className="settings-account-error">{mutation.error.message}</div>}
      <div className="settings-help">
        {changing && !currentPassword
          ? '请输入当前密码以确认修改。'
          : '保存后需要重新登录，登录后会回到这里。'}
      </div>
      <div>
        <Button type="primary" htmlType="submit" theme="outline" loading={mutation.isPending} disabled={!canSave}>
          保存个人账号
        </Button>
      </div>
    </form>
  )
}
