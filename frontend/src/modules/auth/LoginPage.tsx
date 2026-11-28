import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
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

import { dingtalkAuthorize, dingtalkExchange, fetchMe, login } from '../../shared/api/auth'
import { currentAuthEpoch, useAuthStore } from '../../shared/store/auth'
import { loginReturnPath } from '../../shared/safe-url'
import './login.css'

const DINGTALK_RETURN = 'crm-dingtalk-return'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)
  const setUser = useAuthStore((state) => state.setUser)

  // 账号由管理员分配，登录页不预填凭据。
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  // 钉钉扫码：跳转前、换票中，都让按钮进 loading 态
  const [dingtalkLoading, setDingtalkLoading] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const returnTo = loginReturnPath(searchParams.get('next'))
  const sessionState = searchParams.get('session')
  const reloginMessage = sessionState === 'expired' ? '登录已过期，请重新登录。'
    : sessionState === 'invalid' ? '登录状态已失效，请重新登录。' : undefined
  // 回调只处理一次：换票是**一次性**的，重复跑必然失败，
  // 还会把刚拿到的登录态顶掉。
  const handledCallback = useRef(false)

  const [profileRetry, setProfileRetry] = useState<{ path: string; epoch: number } | null>(null)
  const finishLogin = useCallback(async (path: string, epoch = currentAuthEpoch()) => {
    try {
      const me = await fetchMe()
      if (epoch !== currentAuthEpoch()) return
      setUser(me)
      setProfileRetry(null)
      navigate(path, { replace: true })
    } catch {
      if (epoch === currentAuthEpoch()) setProfileRetry({ path, epoch })
      else Toast.warning('登录状态已失效，请重新登录。')
    }
  }, [navigate, setUser])

  const handleLogin = async () => {
    if (loading || dingtalkLoading) return
    if (!username.trim() || !password) {
      Toast.warning('请输入用户名和密码')
      return
    }
    setLoading(true)
    try {
      const result = await login(username.trim(), password)
      setAuth(result.access_token, null)
      setProfileRetry(null)
      await finishLogin(returnTo)
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  const handleDingTalk = async () => {
    if (loading || dingtalkLoading) return
    setDingtalkLoading(true)
    try {
      const { authorize_url } = await dingtalkAuthorize()
      try {
        sessionStorage.setItem(DINGTALK_RETURN, returnTo)
      } catch { /* 禁用浏览器存储时仍可扫码登录，完成后回工作台。 */ }
      // 跳走之后本页就不在了，不需要收尾 loading
      window.location.href = authorize_url
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '钉钉登录暂不可用')
      setDingtalkLoading(false)
    }
  }

  // 接住从钉钉跳回来的结果：成功带 `dingtalk_ticket`，失败带 `dingtalk_error`。
  // 两者都要**立刻从地址栏清掉**：票据是一次性的，留着刷新一次就白跑一趟；
  // 错误则是"说一次就够"，跟着地址栏会反复弹。
  useEffect(() => {
    if (handledCallback.current) return
    const error = searchParams.get('dingtalk_error')
    const ticket = searchParams.get('dingtalk_ticket')
    if (!error && !ticket) return
    let callbackNext = returnTo
    try {
      callbackNext = loginReturnPath(searchParams.get('next') ?? sessionStorage.getItem(DINGTALK_RETURN))
      sessionStorage.removeItem(DINGTALK_RETURN)
    } catch { /* 禁用浏览器存储时使用本页的返回地址。 */ }
    const clearCallback = () => setSearchParams(callbackNext === '/workbench' ? {} : { next: callbackNext }, { replace: true })

    if (error) {
      handledCallback.current = true
      Toast.error(error)
      clearCallback()
      return
    }
    if (!ticket) return

    handledCallback.current = true
    void (async () => {
      setDingtalkLoading(true)
      try {
        const result = await dingtalkExchange(ticket)
        setAuth(result.access_token, null)
        clearCallback()
        setProfileRetry(null)
        await finishLogin(callbackNext)
      } catch (err) {
        Toast.error(err instanceof Error ? err.message : '钉钉登录失败')
        clearCallback()
      } finally {
        setDingtalkLoading(false)
      }
    })()
  }, [searchParams, setSearchParams, setAuth, finishLogin, returnTo])

  return (
    <div className="crm-login">
      <aside className="crm-login-story" aria-label="销售协作平台">
        <div className="crm-login-brand">
          <img className="crm-login-brand-mark" src="/tianchu-logo.svg?v=tc1" alt="" />
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
          {reloginMessage && <p className="crm-login-session-notice" role="status">{reloginMessage}</p>}
          {(returnTo !== '/workbench' || !reloginMessage) && (
            <p className="crm-login-description">{returnTo !== '/workbench' ? '登录后将返回你刚才打开的页面。' : '从这里，继续今天的业务。'}</p>
          )}

          {profileRetry && (
            <div className="crm-login-session-notice" role="alert">
              登录已验证，暂时无法读取账号权限。
              <button type="button" className="crm-text-action" disabled={loading || dingtalkLoading} onClick={async () => {
                if (currentAuthEpoch() !== profileRetry.epoch) {
                  setProfileRetry(null)
                  Toast.warning('登录状态已失效，请重新登录。')
                  return
                }
                setLoading(true)
                try {
                  await finishLogin(profileRetry.path, profileRetry.epoch)
                } finally {
                  setLoading(false)
                }
              }}>重试读取权限</button>
            </div>
          )}
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

          <div className="crm-login-divider"><span>或</span></div>

          <button
            className="crm-login-dingtalk"
            type="button"
            onClick={() => void handleDingTalk()}
            disabled={loading || dingtalkLoading}
          >
            {dingtalkLoading
              ? <IconLoading className="crm-login-spinner" aria-hidden="true" />
              : (
                <svg className="crm-login-dingtalk-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3.5 8.5v-3a2 2 0 0 1 2-2h3" />
                  <path d="M15.5 3.5h3a2 2 0 0 1 2 2v3" />
                  <path d="M20.5 15.5v3a2 2 0 0 1-2 2h-3" />
                  <path d="M8.5 20.5h-3a2 2 0 0 1-2-2v-3" />
                  <rect x="7.5" y="7.5" width="3.2" height="3.2" rx="1" />
                  <rect x="13.3" y="13.3" width="3.2" height="3.2" rx="1" />
                </svg>
              )}
            <span>{dingtalkLoading ? '正在前往钉钉…' : '用钉钉扫码登录'}</span>
          </button>

          <p className="crm-login-admin-note"><IconShieldStroked aria-hidden="true" />账号由管理员统一分配</p>
        </div>
        <span className="crm-login-footer">销售协作平台 · SALES CRM</span>
      </main>
    </div>
  )
}
