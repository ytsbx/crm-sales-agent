import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Toast } from '@douyinfe/semi-ui'

import { fetchMe, login } from '../../shared/api/auth'
import { useAuthStore } from '../../shared/store/auth'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)
  const setUser = useAuthStore((state) => state.setUser)

  const [username, setUsername] = useState('zhangsan')
  const [password, setPassword] = useState('123456')
  const [loading, setLoading] = useState(false)

  const handleLogin = async () => {
    if (!username.trim() || !password) {
      Toast.warning('请输入用户名和密码')
      return
    }
    setLoading(true)
    try {
      const result = await login(username.trim(), password)
      setAuth(result.access_token, null)
      try {
        const me = await fetchMe()
        setUser(me)
      } catch {
        // 用户信息拉取失败不阻塞登录
      }
      navigate('/workbench')
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-title">报价驱动型销售 CRM</div>
        <div className="login-hint">
          演示账号：admin / admin123（管理员）
          <br />
          zhangsan / 123456（业务员）　lisi / 123456（主管）
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Input
            size="large"
            placeholder="用户名"
            value={username}
            onChange={setUsername}
            onEnterPress={handleLogin}
          />
          <Input
            size="large"
            mode="password"
            placeholder="密码"
            value={password}
            onChange={setPassword}
            onEnterPress={handleLogin}
          />
          <Button theme="solid" size="large" loading={loading} onClick={handleLogin} block>
            登录
          </Button>
        </div>
      </div>
    </div>
  )
}
