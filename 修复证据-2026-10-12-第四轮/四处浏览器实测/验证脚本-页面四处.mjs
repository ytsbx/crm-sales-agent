#!/usr/bin/env node
/**
 * 只读验证：本轮补的四处「为什么点不了 / 为什么没有」说明，真的出现在页面上吗？
 * 用业务员 zhangsan（SALES：没有 customer:export、没有 product:manage）实测。
 * 判据：① 灰字在页面上；② 点一下有反应（提示条）。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const APP_BASE = process.env.APP_BASE ?? 'http://127.0.0.1:5275'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8059'
const OUT_DIR = process.argv[2] ?? join(tmpdir(), 'crm-blocked-hint-verify')
const CDP_PORT = Number(process.env.CDP_PORT ?? 9497)
const PROFILE_DIR = join(tmpdir(), `crm-verify-${CDP_PORT}`)

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

// 业务员：没有 customer:export、没有 product:manage
const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'zhangsan', password: '123456' }),
}).then((r) => r.json())
if (login?.code !== 0) throw new Error(`登录失败：${login?.message}`)
const me = await fetch(`${API_BASE}/api/v1/auth/me`, {
  headers: { Authorization: `Bearer ${login.data.access_token}` },
}).then((r) => r.json())
const auth = { Authorization: `Bearer ${login.data.access_token}` }
const products = await fetch(`${API_BASE}/api/v1/products?page=1&page_size=5`, { headers: auth }).then((r) => r.json())
const productId = products?.data?.items?.[0]?.id
console.log(`登录用户：${me.data?.name} 角色=${JSON.stringify(me.data?.roles)} 产品 id=${productId}`)

mkdirSync(OUT_DIR, { recursive: true })
const browser = spawn(
  resolveBrowser(),
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${PROFILE_DIR}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const log = []
const record = (line) => {
  console.log(line)
  log.push(line)
}
let failures = 0
function expect(ok, label) {
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
  const target = await fetch(
    `http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(`${APP_BASE}/login`)}`,
    { method: 'PUT' },
  ).then((r) => r.json())
  const client = await connect(target.webSocketDebuggerUrl)
  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
  await client.send('Runtime.evaluate', {
    expression: `localStorage.setItem('crm-auth', ${JSON.stringify(
      JSON.stringify({ state: { token: login.data.access_token, user: me.data }, version: 0 }),
    )})`,
  })

  async function evaluate(expression) {
    const result = await client.send('Runtime.evaluate', {
      expression: `JSON.stringify((() => { ${expression} })())`,
      returnByValue: true,
      awaitPromise: true,
    })
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
      const probe = await client.send('Runtime.evaluate', {
        expression: 'document.body.innerText.trim().length > 200',
        returnByValue: true,
      })
      if (probe.result.value) break
      await sleep(300)
    }
    await sleep(1500)
  }
  /** 按文字找元素，返回坐标 */
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

  // ---------- 1. 客户中心：导出灰着 + 一行灰字 ----------
  record('\n=== 1. /customers 没有「客户导出」权限 ===')
  await goto('/customers')
  const exportBtn = await locate(`Array.from(document.querySelectorAll('button')).find(b => (b.innerText||'').trim() === '导出')`)
  expect(Boolean(exportBtn), `导出按钮常驻（不再消失）：${JSON.stringify(exportBtn)}`)
  expect(exportBtn?.disabled === true, `导出按钮是灰的（disabled=${exportBtn?.disabled}）`)
  const toolbarText = await evaluate(`
    const bar = document.querySelector('.toolbar');
    return bar ? bar.innerText.replace(/\\s+/g, ' ').trim() : '';
  `)
  expect(toolbarText.includes('导出需要单独授权，请联系管理员开通「客户导出」权限。'), `工具栏灰字在：${JSON.stringify(toolbarText.slice(0, 200))}`)
  await shoot('1-客户中心-导出灰着且有说明.png')
  await clickAt(exportBtn)
  const t1 = await toasts()
  expect(t1.some((t) => t.includes('导出需要单独授权')), `点灰按钮有反应：${JSON.stringify(t1)}`)
  const modalOpened = await evaluate(`return Boolean(document.querySelector('.semi-modal-content'));`)
  expect(modalOpened === false, `点灰按钮没有打开导出弹窗（disabled 没有被绕过，modal=${modalOpened}）`)
  await shoot('2-客户中心-点灰按钮弹提示.png')

  // 导出弹窗（管理员才有入口，这里改走 URL 不可行——直接检查源码里的确认键说明由 tsc/代码审阅覆盖）

  // ---------- 2. 产品中心：没有「产品管理」权限 ----------
  record('\n=== 2. /products 没有「产品管理」权限 ===')
  await goto('/products')
  const pText = await evaluate(`const bar = document.querySelector('.toolbar'); return bar ? bar.innerText.replace(/\\s+/g,' ').trim() : '';`)
  expect(pText.includes('「产品管理」权限'), `产品中心灰字在：${JSON.stringify(pText.slice(0, 220))}`)
  expect(!pText.includes('批量导入'), '原控件组仍然不渲染（判据没动）')
  const pHint = await locate(`Array.from(document.querySelectorAll('.toolbar .blocked-hint-click')).find(n => (n.innerText||'').includes('产品管理'))`)
  await shoot('3-产品中心-灰字说明.png')
  expect(Boolean(pHint), '灰字可点（blocked-hint-click）')
  if (pHint) {
    await clickAt(pHint)
    const t2 = await toasts()
    expect(t2.some((t) => t.includes('产品管理')), `点灰字有反应：${JSON.stringify(t2)}`)
    await shoot('4-产品中心-点灰字弹提示.png')
  }

  // ---------- 3. 定制需求：更多菜单里的缺项说明 ----------
  // 业务员自己名下没有需求行（data_scope=self），所以这一步换成 admin 的会话读数据，
  // 但把 localStorage 里的角色/权限**改成**没有 order:manage、sample:manage 的样子 ——
  // 前端显隐只看这份本地权限副本，正好用来验证"菜单项消失时有没有说明"。
  record('\n=== 3. /inquiries 「更多」菜单缺项说明 ===')
  const adminLogin = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  }).then((r) => r.json())
  const adminMe = await fetch(`${API_BASE}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${adminLogin.data.access_token}` },
  }).then((r) => r.json())
  const fakeUser = {
    ...adminMe.data,
    roles: ['salesperson'],
    permissions: (adminMe.data.permissions ?? []).filter(
      (code) => !['order:manage', 'sample:manage', 'product:manage'].includes(code),
    ),
  }
  await client.send('Runtime.evaluate', {
    expression: `localStorage.setItem('crm-auth', ${JSON.stringify(
      JSON.stringify({ state: { token: adminLogin.data.access_token, user: fakeUser }, version: 0 }),
    )})`,
  })
  await goto('/inquiries')
  const opened = await evaluate(`
    const rows = Array.from(document.querySelectorAll('.semi-table-row'));
    if (!rows.length) return 'no-row: ' + document.body.innerText.replace(/\\s+/g,' ').slice(0, 200);
    for (const row of rows) {
      const more = Array.from(row.querySelectorAll('a')).find(n => (n.innerText||'').trim() === '更多');
      if (more) { more.click(); return 'opened in row: ' + (row.innerText||'').replace(/\\s+/g,' ').slice(0, 60); }
    }
    return 'no-more（' + rows.length + ' 行；首行=' + (rows[0].innerText||'').replace(/\\s+/g,' ').slice(0, 120) + '）';
  `)
  record(`打开更多：${opened}`)
  await sleep(900)
  const menu = await evaluate(`
    const m = document.querySelector('.semi-dropdown-menu');
    return m ? m.innerText.replace(/\\s+/g,' ').trim() : null;
  `)
  record(`菜单文字：${JSON.stringify(menu)}`)
  const hasNote = Boolean(menu && (menu.includes('权限') || menu.includes('当前状态') || menu.includes('第 1 版')))
  expect(hasNote, '菜单末尾有缺项说明（灰字，不可点）')
  await shoot('5-定制需求-更多菜单缺项说明.png')

  // ---------- 4. 产品详情：附件面板无写权限（writePermission=product:manage） ----------
  if (productId) {
    record(`\n=== 4. /products/${productId} 附件面板（业务员没有 product:manage） ===`)
    await goto(`/products/${productId}`)
    const aText = await evaluate(`
      const hit = Array.from(document.querySelectorAll('.blocked-hint-click')).find(n => (n.innerText||'').includes('附件'));
      return hit ? hit.innerText.replace(/\\s+/g,' ').trim() : null;
    `)
    record(`附件区说明：${JSON.stringify(aText)}`)
    expect(Boolean(aText && aText.includes('「产品管理」权限')), '附件面板说明了缺哪个权限')
    const uploadBtn = await evaluate(`return Boolean(Array.from(document.querySelectorAll('button')).find(b => (b.innerText||'').trim() === '上传附件'));`)
    expect(uploadBtn === false, '上传按钮仍然不渲染（判据没动）')
    await shoot('6-产品详情-附件无写权限说明.png')
  } else {
    record('（没有产品数据，跳过附件面板实测）')
  }

  writeFileSync(join(OUT_DIR, '验证.log'), `${log.join('\n')}\n`, 'utf8')
  record(`\n结果：${failures === 0 ? '全部通过' : `${failures} 项未通过`}；产物在 ${OUT_DIR}`)
  client.close()
} finally {
  browser.kill()
  await sleep(300)
}
process.exit(failures === 0 ? 0 : 1)
