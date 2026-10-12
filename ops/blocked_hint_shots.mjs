#!/usr/bin/env node
/**
 * 定向交互截图：「从来源建立订单草稿 / 申请打样」的三种受阻形态。
 *
 * 主人 2026-10-12 反馈：「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，
 * 类似的情况都要修」。这个脚本就为这句话取证：**每一种让确认键变灰的情形，
 * 界面上都必须有一句说清原因的话**，而且点了要有反应。
 *
 * 覆盖三种（`QUOTE_ID` / `INQUIRY_NO` 由调用方给）：
 *   A. 来源一条明细都没有（主人截图那一屏，`QUOTE_ID`）
 *   B. 来源没记原采购数量 → 本次数量为空、确认键灰（`INQUIRY_NO`，走需求页「更多 › 建订单草稿」）
 *   C. 数量填回去之后，灰按钮自己恢复可用（不用刷新、不用重开弹窗）
 *
 * 用法（只读；确认键全程是灰的或只做「填回数量让按钮亮起来」这一个动作，
 * **不会真的建草稿**。要真的建草稿请另外显式指定 ALLOW_SUBMIT=1）：
 *   APP_BASE=http://127.0.0.1:5275 API_BASE=http://127.0.0.1:8059 \
 *     QUOTE_ID=8 INQUIRY_NO=XQ202610120002 node ops/blocked_hint_shots.mjs 修复证据-xxx/
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const APP_BASE = process.env.APP_BASE ?? 'http://127.0.0.1:5275'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8059'
const QUOTE_ID = process.env.QUOTE_ID ?? '8'
/** 需求列表的主键是**需求编号**（表格里显示的就是它，不是自增 id）。 */
const INQUIRY_NO = process.env.INQUIRY_NO ?? 'XQ202610120002'
const OUT_DIR = process.argv[2] ?? join(tmpdir(), 'crm-blocked-hint')
const CDP_PORT = Number(process.env.CDP_PORT ?? 9480)
const PROFILE_DIR = join(tmpdir(), `crm-hint-shots-${CDP_PORT}`)

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

const login = await fetch(`${API_BASE}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin123' }),
}).then((r) => r.json())
if (login?.code !== 0) throw new Error(`登录失败：${login?.message ?? '未知'}`)
const me = await fetch(`${API_BASE}/api/v1/auth/me`, {
  headers: { Authorization: `Bearer ${login.data.access_token}` },
}).then((r) => r.json())

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
function record(line) {
  console.log(line)
  log.push(line)
}

try {
  for (let i = 0; i < 60; i += 1) {
    try {
      const probe = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
      if (probe.ok) break
    } catch {
      /* 等浏览器起来 */
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
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  })
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
    if (result.exceptionDetails) {
      throw new Error(`页面里报错：${result.exceptionDetails.exception?.description ?? '未知'}`)
    }
    return JSON.parse(result.result.value)
  }

  async function shoot(name) {
    const shot = await client.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    writeFileSync(join(OUT_DIR, name), Buffer.from(shot.data, 'base64'))
    record(`✓ 截图 ${name}`)
  }

  async function goto(path) {
    // 带时间戳：Vite dev 的 HMR 有时还没把改动推下来，硬刷新一次保证测的是**当前源码**
    // （第一轮实测就踩过：模块里已经有 onClick，页面上跑的还是上一版，白折腾一轮）。
    const url = `${APP_BASE}${path}${path.includes('?') ? '&' : '?'}shot=${Date.now()}`
    await client.send('Page.navigate', { url })
    for (let i = 0; i < 80; i += 1) {
      const probe = await client.send('Runtime.evaluate', {
        expression: 'document.body.innerText.trim().length > 200',
        returnByValue: true,
      })
      if (probe.result.value) break
      await sleep(300)
    }
    await sleep(800)
    // 硬重载一次：Vite dev 的 HMR 有时滞后，页面跑的还是上一版模块
    //（本轮实测踩过一次，白排查两轮），重载才能保证测的是**当前源码**。
    await client.send('Page.reload', { ignoreCache: true })
    for (let i = 0; i < 80; i += 1) {
      const probe = await client.send('Runtime.evaluate', {
        expression: 'document.body.innerText.trim().length > 200',
        returnByValue: true,
      })
      if (probe.result.value) break
      await sleep(300)
    }
    await sleep(1200)
  }

  /** 弹窗此刻的真实状态：确认键、说明文字、正文。 */
  async function dialogState() {
    return evaluate(`
      const dialog = document.querySelector('.semi-modal-content');
      if (!dialog) return { dialog: false };
      const ok = dialog.querySelector('.semi-modal-footer button[aria-label="confirm"]');
      const hint = dialog.querySelector('.blocked-hint');
      return {
        dialog: true,
        okText: ok ? (ok.innerText || '').trim() : null,
        okDisabled: ok ? Boolean(ok.disabled) : null,
        hintText: hint ? hint.innerText.trim() : null,
        bodyText: (dialog.querySelector('.semi-modal-body')?.innerText ?? '').trim(),
      };
    `)
  }

  async function clickByText(text) {
    return evaluate(`
      const hit = Array.from(document.querySelectorAll('button, a, .semi-dropdown-item, li')).find(n => (n.innerText || '').trim() === ${JSON.stringify(text)});
      if (!hit) return 'not-found';
      hit.click();
      return 'clicked';
    `)
  }

  /**
   * 点那段说明文字，然后**立刻**截图 —— 提示条默认 3 秒后自己消失，
   * 「点 → 等 → 截图」这条链路实测抓不稳（等久了拍空、等短了还没渲染）。
   *
   * 用 `element.click()` 而不是 CDP 的 mousePressed/mouseReleased：
   * 前者派发的就是这个元素上**真实的 DOM click 事件**（React 的 onClick 收到的
   * 与真人点击完全一样），路径更短、更确定性；坐标命中测试单独用 elementFromPoint
   * 报告出来，保证"点的是哪儿"有据可查。
   */
  /**
   * 找到弹窗里那段「可点」的说明并点它，随后由调用方立刻截图。
   *
   * ⚠️ 两个坑（都实测踩过，写下来免得下次再踩）：
   * 1. 弹窗里可能有**两处** `.blocked-hint`（正文里的具体说明 + 页脚的汇总）。
   *    要点的是页脚那处 —— 它才在 `.blocked-hint-click` 包裹层里。
   *    所以按包裹层反查，而不是 `querySelector('.blocked-hint')`（那会拿到正文里那处，
   *    它的父级是 `.semi-modal-body`，点了当然没反应）。
   * 2. 提示条默认 3 秒后自己消失，「点 → 等 → 截图」抓不稳；
   *    这里点完只等 400ms 就交给调用方截图。
   */
  async function clickHint() {
    const probe = await evaluate(`
      // 优先点页脚那段汇总（它一定在包裹层里）；空来源时页脚没有可点的东西，
      // 就点正文里那段具体说明（它自己也接了同一个 onClick）。
      const wrapper = document.querySelector('.semi-modal-content .blocked-hint-click')
        ?? Array.from(document.querySelectorAll('.semi-modal-content .blocked-hint'))
          .find((n) => n.getAttribute('role') === 'alert')
      if (!wrapper) return null;
      const hint = wrapper.querySelector('.blocked-hint') ?? wrapper;
      const r = hint.getBoundingClientRect();
      const x = Math.round(r.x + r.width / 2);
      const y = Math.round(r.y + 20);
      const at = document.elementFromPoint(x, y);
      wrapper.click();
      return {
        x, y, w: Math.round(r.width), h: Math.round(r.height),
        atPoint: at ? at.tagName + '.' + String(at.className).slice(0, 40) : null,
        insideHint: at ? hint.contains(at) : false,
        wrapperClass: wrapper.className,
        hintText: (hint.innerText || '').trim().slice(0, 40),
      };
    `)
    if (!probe) return 'no-hint'
    await sleep(400)
    return `clicked(${JSON.stringify(probe)})`
  }

  async function toasts() {
    // ⚠️ 选择器要认准 `.semi-toast-content-text`：上一版只查 `.semi-toast-content`，
    // 提示条明明弹出来了却读成空数组，白排查了两轮。
    return evaluate(`
      return Array.from(document.querySelectorAll('.semi-toast-content-text'))
        .map(n => (n.innerText || '').trim())
        .filter((text, index, all) => text && all.indexOf(text) === index);
    `)
  }

  async function closeDialog() {
    await evaluate(`
      const cancel = document.querySelector('.semi-modal-footer button[aria-label="cancel"]');
      if (cancel) cancel.click();
      return 'ok';
    `)
    await sleep(700)
  }

  // ---------- A. 来源一条明细都没有（主人截图那一屏） ----------
  record(`\n=== A. 报价 Q${QUOTE_ID}：来源一条明细都没有 ===`)
  await goto(`/quotes/${QUOTE_ID}`)
  record(`打开弹窗 → ${await clickByText('按此版本建订单草稿')}`)
  await sleep(1400)
  const a = await dialogState()
  record(`确认键「${a.okText}」disabled=${a.okDisabled}`)
  record(`弹窗里的说明：${JSON.stringify(a.hintText)}`)
  await shoot('01-A-来源没有明细-确认键灰但有说明.png')
  const aClick = await clickHint()
  record(`点说明 → ${aClick.split('|')[0]}`)
  await shoot('02-A-点一下有反应.png')
  const aToast = await toasts()
  record(`提示条：${JSON.stringify(aToast)}`)
  writeFileSync(join(OUT_DIR, 'A-弹窗文字.txt'), `确认键：${a.okText}（disabled=${a.okDisabled}）\n\n说明：${a.hintText}\n\n提示条：${JSON.stringify(aToast)}\n\n正文：\n${a.bodyText}`, 'utf8')
  await closeDialog()

  // ---------- B. 来源没记原采购数量 ----------
  record(`\n=== B. 定制需求 ${INQUIRY_NO}：来源没记原采购数量 ===`)
  await goto('/inquiries')
  const opened = await evaluate(`
    const rows = Array.from(document.querySelectorAll('.semi-table-row'));
    const row = rows.find(n => (n.innerText || '').includes(${JSON.stringify(INQUIRY_NO)}));
    if (!row) return 'row-not-found（表里第 ' + rows.length + ' 行，首行=' + JSON.stringify((rows[0]?.innerText ?? '').slice(0, 90)) + '）';
    const more = Array.from(row.querySelectorAll('a')).find(n => (n.innerText || '').trim() === '更多');
    if (!more) return 'more-not-found';
    more.click();
    return 'opened-menu';
  `)
  record(`打开行内「更多」→ ${opened}`)
  await sleep(900)
  record(`点「建订单草稿」→ ${await clickByText('建订单草稿')}`)
  await sleep(1600)
  const b = await dialogState()
  record(`确认键「${b.okText}」disabled=${b.okDisabled}`)
  record(`弹窗里的说明：${JSON.stringify(b.hintText)}`)
  await shoot('03-B-原数量未记录-数量框为空且说明到位.png')
  const bClick = await clickHint()
  record(`点说明 → ${bClick.split('|')[0]}`)
  await shoot('04-B-点一下有反应.png')
  const bToast = await toasts()
  record(`提示条：${JSON.stringify(bToast)}`)
  writeFileSync(join(OUT_DIR, 'B-弹窗文字.txt'), `确认键：${b.okText}（disabled=${b.okDisabled}）\n\n说明：${b.hintText}\n\n提示条：${JSON.stringify(bToast)}\n\n正文：\n${b.bodyText}`, 'utf8')

  // ---------- C. 填回数量 → 按钮自己亮起来 ----------
  record('\n=== C. 填回数量后确认键恢复可用（不用刷新、不用重开） ===')
  const filled = await evaluate(`
    const input = document.querySelector('.semi-modal-content input[role="spinbutton"], .semi-modal-content .semi-inputnumber input');
    if (!input) return 'no-input';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '20');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'filled';
  `)
  record(`填数量 20 → ${filled}`)
  await sleep(900)
  const c = await dialogState()
  record(`确认键 disabled=${c.okDisabled}（期望 false）；说明块：${c.hintText === null ? '已消失（期望）' : JSON.stringify(c.hintText)}`)
  await shoot('05-C-数量填回后-确认键恢复可用.png')
  writeFileSync(join(OUT_DIR, 'C-弹窗文字.txt'), `确认键 disabled=${c.okDisabled}（期望 false）\n说明块：${c.hintText === null ? '已消失（期望）' : c.hintText}\n\n正文：\n${c.bodyText}`, 'utf8')

  writeFileSync(join(OUT_DIR, '本轮浏览器实测.log'), `${log.join('\n')}\n`, 'utf8')
  record(`\n✓ 记录 ${join(OUT_DIR, '本轮浏览器实测.log')}`)
  client.close()
} finally {
  browser.kill()
  await sleep(300)
}
