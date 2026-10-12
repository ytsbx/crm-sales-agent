#!/usr/bin/env node
/**
 * 只读验证（管理员会话，用于打开需要权限的弹窗）：
 *   5. 客户中心「导出」弹窗：确认键灰着时，弹窗里有没有说明（未选用途 / 选了「其他」没填说明）；
 *      点说明有没有反应。
 *   6. 定制需求「编辑」弹窗：客户没选时「关联商机」灰着，下面有没有一行灰字；点它有没有反应。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const APP_BASE = process.env.APP_BASE ?? 'http://127.0.0.1:5275'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8059'
const OUT_DIR = process.argv[2] ?? join(tmpdir(), 'crm-blocked-hint-verify2')
const CDP_PORT = Number(process.env.CDP_PORT ?? 9498)
const PROFILE_DIR = join(tmpdir(), `crm-verify2-${CDP_PORT}`)

function resolveBrowser() {
  const candidates =
    process.platform === 'darwin'
      ? [
          '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        ]
      : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium']
  const hit = candidates.find((p) => p && existsSync(p))
  if (!hit) throw new Error('找不到 Edge/Chrome，设置 EDGE_BIN')
  return hit
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl)
    const pending = new Map()
    socket.addEventListener('open', () =>
      resolve({
        send(method, params = {}) {
          const id = Math.floor(Math.random() * 1e9)
          return new Promise((res, rej) => {
            pending.set(id, { resolve: res, reject: rej })
            socket.send(JSON.stringify({ id, method, params }))
          })
        },
        close: () => socket.close(),
      }),
    )
    socket.addEventListener('error', () => reject(new Error('连不上浏览器')))
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      const slot = pending.get(message.id)
      if (!slot) return
      pending.delete(message.id)
      if (message.error) slot.reject(new Error(JSON.stringify(message.error)))
      else slot.resolve(message.result)
    })
  })
}

const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin123' }),
}).then((r) => r.json())
const me = await fetch(`${API_BASE}/api/v1/auth/me`, {
  headers: { Authorization: `Bearer ${login.data.access_token}` },
}).then((r) => r.json())
console.log(`登录用户：${me.data?.name}`)

mkdirSync(OUT_DIR, { recursive: true })
const browser = spawn(
  resolveBrowser(),
  ['--headless=new', '--disable-gpu', '--no-first-run', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE_DIR}`, 'about:blank'],
  { stdio: 'ignore' },
)
const log = []
const record = (line) => {
  console.log(line)
  log.push(line)
}
let failures = 0
const expect = (ok, label) => {
  if (!ok) failures += 1
  record(`${ok ? '✓' : '✗'} ${label}`)
}

try {
  for (let i = 0; i < 60; i += 1) {
    try {
      if ((await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok) break
    } catch {
      /* 等浏览器 */
    }
    await sleep(250)
  }
  const target = await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(`${APP_BASE}/login`)}`, { method: 'PUT' }).then((r) => r.json())
  const client = await connect(target.webSocketDebuggerUrl)
  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
  await client.send('Runtime.evaluate', {
    expression: `localStorage.setItem('crm-auth', ${JSON.stringify(JSON.stringify({ state: { token: login.data.access_token, user: me.data }, version: 0 }))})`,
  })

  async function evaluate(expression) {
    const result = await client.send('Runtime.evaluate', { expression: `JSON.stringify((() => { ${expression} })())`, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? '页面报错')
    return JSON.parse(result.result.value)
  }
  async function shoot(name) {
    const shot = await client.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    writeFileSync(join(OUT_DIR, name), Buffer.from(shot.data, 'base64'))
  }
  async function goto(path) {
    await client.send('Page.navigate', { url: `${APP_BASE}${path}${path.includes('?') ? '&' : '?'}shot=${Date.now()}` })
    for (let i = 0; i < 80; i += 1) {
      const probe = await client.send('Runtime.evaluate', { expression: 'document.body.innerText.trim().length > 200', returnByValue: true })
      if (probe.result.value) break
      await sleep(300)
    }
    await sleep(1600)
  }
  async function locate(selectorJs) {
    return evaluate(`
      const hit = ${selectorJs};
      if (!hit) return null;
      hit.scrollIntoView({ block: 'center' });
      const r = hit.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), text: (hit.innerText || '').trim(), disabled: Boolean(hit.disabled) };
    `)
  }
  async function clickAt(point) {
    await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y })
    await client.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 })
    await client.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 })
    await sleep(900)
  }
  async function toasts() {
    return evaluate(`
      return Array.from(document.querySelectorAll('.semi-toast-content-text, .semi-toast-content'))
        .map(n => (n.innerText || '').trim())
        .filter((t, i, all) => t && all.indexOf(t) === i);
    `)
  }

  // ---------- 5. 导出弹窗的确认键说明 ----------
  record('\n=== 5. /customers 导出弹窗：确认键灰着的原因 ----------')
  await goto('/customers')
  const exportBtn = await locate(`Array.from(document.querySelectorAll('button')).find(b => (b.innerText||'').trim() === '导出')`)
  expect(Boolean(exportBtn && !exportBtn.disabled), `管理员看到的导出按钮可用（disabled=${exportBtn?.disabled}）`)
  await clickAt(exportBtn)
  await sleep(900)
  const okBtn = await evaluate(`
    const ok = document.querySelector('.semi-modal-content .semi-modal-footer button[aria-label="confirm"]');
    return ok ? { text: ok.innerText.trim(), disabled: Boolean(ok.disabled) } : null;
  `)
  expect(okBtn?.disabled === true, `确认键灰着（${JSON.stringify(okBtn)}）`)
  const hint1 = await evaluate(`const h = document.querySelector('.semi-modal-content .blocked-hint'); return h ? h.innerText.replace(/\\s+/g,' ').trim() : null;`)
  record(`弹窗说明：${JSON.stringify(hint1)}`)
  expect(Boolean(hint1 && hint1.includes('请选择导出用途。')), '未选用途时说明了「请选择导出用途。」')
  await shoot('7-导出弹窗-未选用途说明.png')
  // 选用途「其他」（真鼠标点击：Semi 的选项列表在 portal 里）
  const selectTrigger = await locate(`document.querySelector('.semi-modal-content .semi-select')`)
  await clickAt(selectTrigger)
  await sleep(700)
  const otherOption = await locate(`Array.from(document.querySelectorAll('.semi-select-option')).find(n => (n.innerText||'').trim() === '其他')`)
  expect(Boolean(otherOption), '找到「其他」选项')
  await clickAt(otherOption)
  await sleep(700)
  const hint2 = await evaluate(`const h = document.querySelector('.semi-modal-content .blocked-hint'); return h ? h.innerText.replace(/\\s+/g,' ').trim() : null;`)
  record(`选了「其他」后：${JSON.stringify(hint2)}`)
  expect(Boolean(hint2 && hint2.includes('必须填写导出用途说明')), '选「其他」没填说明时补上了第二条原因')
  await shoot('8-导出弹窗-其他未填说明.png')
  const hintBox = await locate(`document.querySelector('.semi-modal-content .blocked-hint')`)
  await clickAt(hintBox)
  const t5 = await toasts()
  expect(t5.some((t) => t.includes('还不能导出')), `点说明有反应：${JSON.stringify(t5)}`)
  await shoot('9-导出弹窗-点说明弹提示.png')

  // ---------- 6. 编辑弹窗里「关联商机」的说明 ----------
  record('\n=== 6. /inquiries 编辑弹窗：关联商机灰着的原因 ----------')
  await goto('/inquiries')
  const editLink = await evaluate(`
    const rows = Array.from(document.querySelectorAll('.semi-table-row'));
    for (const row of rows) {
      const edit = Array.from(row.querySelectorAll('a')).find(n => (n.innerText||'').trim() === '编辑');
      if (edit) { edit.click(); return 'clicked'; }
    }
    return 'no-edit';
  `)
  record(`打开编辑弹窗：${editLink}`)
  await sleep(1200)
  // 这一行的客户可能已经填了（填了的话「关联商机」本来就不该灰）。
  // 先把客户清掉，再看灰字有没有跟着出现。
  const state = await evaluate(`
    const modal = document.querySelector('.semi-modal-content');
    if (!modal) return { modal: false };
    return {
      modal: true,
      disabledSelects: modal.querySelectorAll('.semi-select-disabled').length,
      selects: modal.querySelectorAll('.semi-select').length,
      clearlike: Array.from(modal.querySelectorAll('[class*="clear"]')).map(n => n.className),
      body: (modal.querySelector('.semi-modal-body')?.innerText ?? '').replace(/\\s+/g,' ').slice(0, 160),
    };
  `)
  record(`弹窗状态：${JSON.stringify(state)}`)
  if (state.modal && state.disabledSelects === 0) {
    // 清掉客户：先悬停让清除按钮出现，再点它
    const firstSelect = await locate(`document.querySelector('.semi-modal-content .semi-select')`)
    await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: firstSelect.x, y: firstSelect.y })
    await sleep(400)
    const clearIcon = await locate(`document.querySelector('.semi-modal-content .semi-select-clear')`)
    record(`清除按钮：${JSON.stringify(clearIcon)}`)
    if (clearIcon && clearIcon.x > 0) await clickAt(clearIcon)
    await sleep(700)
  }
  const state2 = await evaluate(`
    const modal = document.querySelector('.semi-modal-content');
    return { disabledSelects: modal ? modal.querySelectorAll('.semi-select-disabled').length : -1 };
  `)
  record(`清掉客户后：${JSON.stringify(state2)}`)
  const grayLine = await evaluate(`
    const modal = document.querySelector('.semi-modal-content');
    if (!modal) return null;
    const hit = Array.from(modal.querySelectorAll('div')).find(n => (n.innerText||'').trim() === '先选客户，才能关联该客户的商机');
    return hit ? hit.innerText.trim() : null;
  `)
  expect(Boolean(grayLine), `编辑弹窗里有灰字：${JSON.stringify(grayLine)}`)
  const disabledSelect = await locate(`document.querySelector('.semi-modal-content .semi-select-disabled')`)
  expect(Boolean(disabledSelect), '关联商机下拉是灰的（判据没动）')
  await shoot('10-编辑弹窗-关联商机灰字.png')
  if (disabledSelect) {
    await clickAt(disabledSelect)
    const t6 = await toasts()
    expect(t6.some((t) => t.includes('先选客户，才能关联该客户的商机')), `点灰下拉有反应：${JSON.stringify(t6)}`)
    await shoot('11-编辑弹窗-点灰下拉弹提示.png')
  }

  writeFileSync(join(OUT_DIR, '验证.log'), `${log.join('\n')}\n`, 'utf8')
  record(`\n结果：${failures === 0 ? '全部通过' : `${failures} 项未通过`}；产物在 ${OUT_DIR}`)
  client.close()
} finally {
  browser.kill()
  await sleep(300)
}
process.exit(failures === 0 ? 0 : 1)
