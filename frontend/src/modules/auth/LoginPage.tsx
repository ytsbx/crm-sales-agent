import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Toast } from '@douyinfe/semi-ui'
import {
  IconArrowRight,
  IconArrowUpRight,
  IconBox,
  IconEyeClosedStroked,
  IconEyeOpenedStroked,
  IconFile,
  IconLoading,
  IconLockStroked,
  IconShieldStroked,
  IconUserGroup,
  IconUserStroked,
} from '@douyinfe/semi-icons'

import { fetchMe, login } from '../../shared/api/auth'
import { currentAuthEpoch, useAuthStore } from '../../shared/store/auth'
import './login.css'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)
  const setUser = useAuthStore((state) => state.setUser)

  // 账号由管理员分配，登录页不预填凭据。
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)

  const handleLogin = async () => {
    if (loading) return
    if (!username.trim() || !password) {
      Toast.warning('请输入用户名和密码')
      return
    }
    setLoading(true)
    try {
      const result = await login(username.trim(), password)
      setAuth(result.access_token, null)
      // 记住"这次登录属于哪一代"：下面这个取用户信息的请求如果回来晚了
      // （人已经点了退出、或又登了一次），就不能再把用户写回去 ——
      // 否则界面会自己回到登录态（第十批 10.12）。
      const epoch = currentAuthEpoch()
      try {
        const me = await fetchMe()
        if (currentAuthEpoch() === epoch) {
          setUser(me)
        }
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
    <div className="crm-login">
      <aside className="crm-login-story" aria-label="销售协作平台">
        <div className="crm-login-brand">
          <img className="crm-login-brand-mark" src="/tianchu-logo.svg" alt="" />
          <span>天初CRM<small>企业协同工作平台</small></span>
        </div>

        <div className="crm-login-message">
          <div className="crm-login-eyebrow"><span aria-hidden="true" />从线索，到长期合作</div>
          <h1>每一次跟进，<br />都有下一步。</h1>
          <p>让客户、报价与订单彼此连接，<br />把精力留给真正重要的合作。</p>
        </div>

        <div className="crm-login-art" aria-hidden="true">
          <div className="crm-login-orbit" />
          <div className="crm-login-orbit crm-login-orbit-outer" />
          <div className="crm-login-orb" />
          <div className="crm-login-flow crm-login-flow-customer">
            <span className="crm-login-flow-icon"><IconUserGroup /></span>
            <span><small>建立连接</small><strong>客户</strong></span>
            <span className="crm-login-flow-dot" />
          </div>
          <div className="crm-login-flow crm-login-flow-quote">
            <span className="crm-login-flow-icon"><IconFile /></span>
            <span><small>把需求变成方案</small><strong>报价</strong></span>
            <IconArrowUpRight />
          </div>
          <div className="crm-login-flow crm-login-flow-order">
            <span className="crm-login-flow-icon"><IconBox /></span>
            <span><small>让合作向前一步</small><strong>订单</strong></span>
          </div>
        </div>

        <div className="crm-login-story-footer">
          <span>连接业务的每一步</span><span>客户 · 商机 · 报价 · 订单</span>
        </div>
      </aside>

      <main className="crm-login-main">
        <span className="crm-login-workspace">团队工作空间</span>
        <div className="crm-login-content">
          <div className="crm-login-welcome">欢迎回来</div>
          <h2>登录你的工作台</h2>
          <p className="crm-login-description">从这里，继续今天的业务。</p>

          <form
            className="crm-login-form"
            aria-label="账号登录"
            aria-busy={loading}
            noValidate
            onSubmit={(event) => { event.preventDefault(); void handleLogin() }}
          >
            <label htmlFor="crm-login-username">用户名</label>
            <div className="crm-login-input">
              <IconUserStroked aria-hidden="true" />
              <input
                id="crm-login-username"
                name="username"
                autoComplete="username"
                placeholder="请输入用户名"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={loading}
                required
              />
            </div>

            <label htmlFor="crm-login-password">密码</label>
            <div className="crm-login-input">
              <IconLockStroked aria-hidden="true" />
              <input
                id="crm-login-password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="请输入密码"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={loading}
                required
              />
              <button
                className="crm-login-password-toggle"
                type="button"
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
                aria-pressed={showPassword}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? <IconEyeClosedStroked /> : <IconEyeOpenedStroked />}
              </button>
            </div>

            <button className="crm-login-submit" type="submit" disabled={loading}>
              {loading && <IconLoading className="crm-login-spinner" aria-hidden="true" />}
              <span>{loading ? '正在登录…' : '进入工作台'}</span>
              {!loading && <IconArrowRight aria-hidden="true" />}
            </button>
          </form>

          <p className="crm-login-admin-note"><IconShieldStroked aria-hidden="true" />账号由管理员统一分配</p>
        </div>
        <span className="crm-login-footer">销售协作平台 · SALES CRM</span>
      </main>
    </div>
  )
}
