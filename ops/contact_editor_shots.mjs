#!/usr/bin/env node
/**
 * 定向交互截图：客户详情「联系人」这一块的录入 / 编辑 / 设主 / 删除。
 *
 * 为什么要它：这一段以前**只有"新建"一个入口** —— 后端早就支持改/删/设主与
 * 部门/座机/微信/备注/主联系人，前端一个都没接；而且脱敏（`138****8000`）
 * 与"编辑入口"凑在一起会**丢数据**（把星号存回去）。所以这里要证明三件事：
 *   ① 新建弹窗字段齐了，能勾「设为主联系人」；
 *   ② 表格有「设为主联系人 / 编辑 / 删除」，点了真的生效；
 *   ③ 脱敏时那几个框锁住，并且**不会**把星号提交上去。
 *
 * 安全约定（踩过坑才写的）：脚本**只碰自己造的那条联系人**，
 * 靠行上的 `data-contact-id` 精确定位它那一行 —— 第一版按"第一条 / 按姓名"找，
 * 结果把库里已有的联系人改了又删了。夹具在收尾无论成败都会被删掉。
 *
 * 用法：
 *   APP_BASE=http://127.0.0.1:5275 API_BASE=http://127.0.0.1:8061 \
 *     CUSTOMER_ID=29 node ops/contact_editor_shots.mjs 证据目录/
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const APP_BASE = process.env.APP_BASE ?? 'http://127.0.0.1:5275'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8061'
const CUSTOMER_ID = process.env.CUSTOMER_ID ?? '29'
const OUT_DIR = process.argv[2] ?? join(tmpdir(), 'crm-contact-editor')
const CDP_PORT = Number(process.env.CDP_PORT ?? 9520)
const FIXTURE_NAME = process.env.FIXTURE_NAME ?? '脚本专用-勿动'
const API = `${API_BASE}/api/v1`
const NL = 'String.fromCharCode(10)'

function resolveBrowser() {
  if (process.env.EDGE_BIN) return process.env.EDGE_BIN
  const candidates =
    process.platform === 'darwin'
      ? [
          '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        ]
      : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium']
  const hit = candidates.find((path) => path && existsSync(path))
  if (!hit) throw new Error('找不到 Edge/Chrome，设置 EDGE_BIN')
  return hit
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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

const login = await fetch(`${API}/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin123' }),
}).then((r) => r.json())
if (login?.code !== 0) throw new Error(`登录失败：${login?.message ?? '未知'}`)
const token = login.data.access_token
const me = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json())

const log = []
function record(line) {
  console.log(line)
  log.push(line)
}
async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  })
  return res.json()
}

mkdirSync(OUT_DIR, { recursive: true })

// 先造脚本专用的联系人；收尾必删
const fixture = await api(`/customers/${CUSTOMER_ID}/contacts`, {
  method: 'POST',
  body: JSON.stringify({ name: FIXTURE_NAME, title: '待改职位', remark: '脚本夹具' }),
})
if (fixture?.code !== 0) throw new Error(`造夹具失败：${fixture?.message}`)
const fixtureId = String(fixture.data.id)
record(`夹具联系人 id=${fixtureId}（脚本只碰它，收尾会删）`)

const browser = spawn(
  resolveBrowser(),
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${join(tmpdir(), `crm-contact-${CDP_PORT}`)}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
)

try {
  for (let i = 0; i < 60; i += 1) {
    try {
      const probe = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
      if (probe.ok) break
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
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await client.send('Runtime.evaluate', {
    expression: `localStorage.setItem('crm-auth', ${JSON.stringify(
      JSON.stringify({ state: { token, user: me.data }, version: 0 }),
    )})`,
  })

  async function evaluate(expression) {
    const result = await client.send('Runtime.evaluate', {
      expression: `JSON.stringify((() => { ${expression} })())`,
      returnByValue: true,
      awaitPromise: true,
    })
    if (result.exceptionDetails) {
      throw new Error(`页面里报错：${result.exceptionDetails.exception?.description ?? '未知'}`)
    }
    return JSON.parse(result.result.value)
  }
  async function probeValue(expression) {
    const result = await client.send('Runtime.evaluate', { expression, returnByValue: true })
    return result.result.value
  }
  async function waitFor(expression, tries = 60) {
    for (let i = 0; i < tries; i += 1) {
      if (await probeValue(expression)) return true
      await sleep(250)
    }
    return false
  }
  async function shoot(name) {
    const shot = await client.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    writeFileSync(join(OUT_DIR, name), Buffer.from(shot.data, 'base64'))
    record(`✓ 截图 ${name}`)
  }
  async function clickText(text, within = 'document') {
    return evaluate(`
      const root = ${within === 'modal' ? "document.querySelector('.semi-modal-content')" : 'document'};
      if (!root) return 'no-scope';
      const hit = Array.from(root.querySelectorAll('button, a, .semi-tabs-tab'))
        .find((n) => (n.innerText || '').trim() === ${JSON.stringify(text)});
      if (!hit) return 'not-found';
      hit.click();
      return 'clicked';
    `)
  }
  /** 只操作夹具那一行（按行上的 data-contact-id）。 */
  async function clickInFixtureRow(linkText) {
    return evaluate(`
      const row = document.querySelector('.semi-table-row[data-contact-id="${fixtureId}"]');
      if (!row) return 'row-not-found';
      const hit = Array.from(row.querySelectorAll('a, button')).find((n) => (n.innerText || '').trim() === ${JSON.stringify(linkText)});
      if (!hit) return 'link-not-found';
      hit.click();
      return 'clicked';
    `)
  }
  async function fixtureRowText() {
    return evaluate(`
      const row = document.querySelector('.semi-table-row[data-contact-id="${fixtureId}"]');
      return row ? (row.innerText || '').split(${NL}).join(' | ') : null;
    `)
  }
  async function allRows() {
    return evaluate(`
      return Array.from(document.querySelectorAll('.semi-table-row'))
        .map((r) => (r.innerText || '').split(${NL}).join(' | '));
    `)
  }
  /**
   * 按字段标签填值。标签可能带必填星号（FormLabel 会额外渲染一个「*」），
   * 所以用"去掉星号与空白后相等"来认；锚点取"自身没有子元素的纯文本节点"。
   *
   * ⚠️ 注入的代码里**不写正则、不写转义字符**：这层字符串要过
   * `JSON.stringify` 与模板串两道，反斜杠会被吃掉（实测报 Nothing to repeat），
   * 所以一律用字符码判断。
   */
  const bareFn = `const bare = (text) => Array.from(text || '').filter((ch) => ch.charCodeAt(0) > 32 && ch !== '*').join('');`
  async function fillByLabel(label, value) {
    return evaluate(`
      const dialog = document.querySelector('.semi-modal-content');
      if (!dialog) return 'no-modal';
      ${bareFn}
      const want = ${JSON.stringify(label)};
      const setValue = (input) => {
        const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, ${JSON.stringify(value)});
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      };
      // 标签锚点：以标签文字**开头**、且内部不含任何输入框的最深节点。
      // 为什么按"开头"而不是"相等"：FormLabel 的标签文字在自己的子 span 里，
      // 这类节点的 innerText 是「姓名*」——要求整串相等就永远匹配不上（实测踩过）。
      const anchors = Array.from(dialog.querySelectorAll('div, span, label'))
        .filter((n) => !n.querySelector('input, textarea') && bare(n.innerText).startsWith(want))
        .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
      for (const anchor of anchors) {
        let scope = anchor;
        for (let depth = 0; depth < 3 && scope; depth += 1) {
          let sib = scope.nextElementSibling;
          while (sib) {
            const found = sib.matches('input, textarea') ? sib : sib.querySelector('input, textarea');
            if (found) { setValue(found); return 'filled'; }
            sib = sib.nextElementSibling;
          }
          scope = scope.parentElement;
        }
      }
      return 'label-not-found';
    `)
  }
  /** 填完立刻自查，别"以为填上了"（第一版就是这么静默丢字段的）。 */
  async function valueOfLabel(label) {
    return evaluate(`
      const dialog = document.querySelector('.semi-modal-content');
      if (!dialog) return null;
      ${bareFn}
      const want = ${JSON.stringify(label)};
      // 标签锚点：以标签文字**开头**、且内部不含任何输入框的最深节点。
      // 为什么按"开头"而不是"相等"：FormLabel 的标签文字在自己的子 span 里，
      // 这类节点的 innerText 是「姓名*」——要求整串相等就永远匹配不上（实测踩过）。
      const anchors = Array.from(dialog.querySelectorAll('div, span, label'))
        .filter((n) => !n.querySelector('input, textarea') && bare(n.innerText).startsWith(want))
        .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
      for (const anchor of anchors) {
        let scope = anchor;
        for (let depth = 0; depth < 3 && scope; depth += 1) {
          let sib = scope.nextElementSibling;
          while (sib) {
            const found = sib.matches('input, textarea') ? sib : sib.querySelector('input, textarea');
            if (found) return found.value;
            sib = sib.nextElementSibling;
          }
          scope = scope.parentElement;
        }
      }
      return null;
    `)
  }

  record(`\n=== 打开客户 #${CUSTOMER_ID} 的「联系人」页签 ===`)
  await client.send('Page.navigate', { url: `${APP_BASE}/customers/${CUSTOMER_ID}?shot=${Date.now()}` })
  await waitFor('document.body.innerText.trim().length > 150')
  await sleep(1200)
  record(`切页签 → ${await clickText('联系人')}`)
  await sleep(1500)
  await shoot('01-联系人页签-每行有设为主编辑删除.png')
  record(`夹具那一行：${JSON.stringify(await fixtureRowText())}`)

  // ---------------- 新建 ----------------
  record('\n=== 新建：字段齐了 + 勾「设为主联系人」 ===')
  record(`点「新建联系人」→ ${await clickText('新建联系人')}`)
  await waitFor("Boolean(document.querySelector('.semi-modal-content input'))")
  await sleep(500)
  const fieldCount = await evaluate(`return document.querySelectorAll('.semi-modal-content input, .semi-modal-content textarea').length;`)
  record(`弹窗里的输入框个数：${fieldCount}（旧版只有 4 个：姓名/职位/手机/邮箱）`)
  await shoot('02-新建弹窗-字段齐了.png')
  for (const [label, value] of [
    ['姓名', '体验核验联系人'],
    ['职位', '采购总监'],
    ['部门', '采购部'],
    ['手机', '13900009999'],
    ['座机', '0571-66668888'],
    ['邮箱', 'qa@example.com'],
    ['微信号', 'qa_wx'],
    ['备注', '只有上午在'],
  ]) {
    const filled = await fillByLabel(label, value)
    record(`填「${label}」→ ${filled}，框里现在是 ${JSON.stringify(await valueOfLabel(label))}`)
  }
  const checked = await evaluate(`
    const dialog = document.querySelector('.semi-modal-content');
    const target = Array.from(dialog.querySelectorAll('.semi-checkbox')).find((b) => (b.innerText || '').includes('设为主联系人'));
    if (!target) return 'checkbox-not-found';
    target.click();
    return 'clicked';
  `)
  record(`勾「设为主联系人」→ ${checked}`)
  await shoot('03-新建弹窗-全部字段与主联系人勾选.png')
  record(`点「保存」→ ${await clickText('保存', 'modal')}`)
  await sleep(2400)
  await shoot('04-新建后-列表出现.png')
  record(`列表：${JSON.stringify(await allRows())}`)

  // ---------------- 编辑 ----------------
  record('\n=== 编辑：只改脚本自己那条 ===')
  record(`点夹具行的「编辑」→ ${await clickInFixtureRow('编辑')}`)
  await waitFor("Boolean(document.querySelector('.semi-modal-content input'))")
  await sleep(600)
  const editTitle = await evaluate(`
    const dialog = document.querySelector('.semi-modal-content');
    return (dialog.querySelector('.semi-modal-header') || dialog).innerText.split(${NL})[0];
  `)
  record(`弹窗标题：${editTitle}`)
  record(`带出的原值：职位=${JSON.stringify(await valueOfLabel('职位'))}，部门=${JSON.stringify(await valueOfLabel('部门'))}`)
  await shoot('05-编辑弹窗-带出了原值.png')
  record(`把职位改成「采购总监（已改）」→ ${await fillByLabel('职位', '采购总监（已改）')}`)
  record(`点「保存」→ ${await clickText('保存', 'modal')}`)
  await sleep(2400)
  await shoot('06-编辑后-改动已生效.png')
  record(`夹具行现在是：${JSON.stringify(await fixtureRowText())}`)

  // ---------------- 设主 ----------------
  record('\n=== 设为主联系人：主位换到夹具这条 ===')
  record(`点夹具行的「设为主联系人」→ ${await clickInFixtureRow('设为主联系人')}`)
  await sleep(2000)
  await shoot('07-设为主联系人-主位换人.png')
  record(`列表：${JSON.stringify(await allRows())}`)

  // ---------------- 删除 ----------------
  record('\n=== 删除：只删脚本自己那条 ===')
  record(`点夹具行的「删除」→ ${await clickInFixtureRow('删除')}`)
  await sleep(800)
  record(`确认 → ${await clickText('确定')}`)
  await sleep(2000)
  await shoot('08-删除后-夹具行消失.png')
  record(`夹具行：${JSON.stringify(await fixtureRowText())}（null = 已消失）`)

  client.close()
} finally {
  browser.kill()
  const leftovers = await api(`/customers/${CUSTOMER_ID}/contacts`)
  if ((leftovers?.data ?? []).some((c) => String(c.id) === fixtureId)) {
    await api(`/contacts/${fixtureId}`, { method: 'DELETE' })
    record(`（兜底）夹具 #${fixtureId} 界面上没删掉，已用接口补删`)
  } else {
    record(`✓ 夹具 #${fixtureId} 已由界面删除，库里没有残留`)
  }
  writeFileSync(join(OUT_DIR, '联系人录入实测.log'), `${log.join('\n')}\n`, 'utf8')
  record(`✓ 记录 ${join(OUT_DIR, '联系人录入实测.log')}`)
  await sleep(300)
}
