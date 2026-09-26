#!/usr/bin/env node
/**
 * 定向截图：打开指定 URL，可选点几下，然后截图。
 *
 * 用法：node ops/shot.mjs /quotes/22 out.png [点击的文字]
 * 用途：验证"有数据"的界面形态（例如带明细的 What-if 面板），
 * 冒烟脚本只覆盖默认路由，个别数据形态要单独看一眼。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

function resolveBrowser() {
  if (process.env.EDGE_BIN) return process.env.EDGE_BIN
  const candidates =
    process.platform === 'win32'
      ? [
          'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
          'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
          join(process.env.LOCALAPPDATA ?? '', 'Microsoft\\Edge\\Application\\msedge.exe'),
        ]
      : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome']
  const hit = candidates.find((path) => path && existsSync(path))
  if (!hit) throw new Error('找不到浏览器，设置 EDGE_BIN')
  return hit
}

const APP_BASE = process.env.APP_BASE ?? 'http://localhost:5173'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8000'
const [path, outName, ...clicks] = process.argv.slice(2)
if (!path || !outName) {
  console.error('用法：node ops/shot.mjs /quotes/22 out.png [点击文字...]')
  process.exit(1)
}

const CDP_PORT = Number(process.env.CDP_PORT ?? 9444)
const PROFILE_DIR = join(tmpdir(), `crm-shot-profile-${CDP_PORT}`)
const OUT_DIR = process.env.SMOKE_OUT ?? join(tmpdir(), 'crm-smoke')
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
const token = login.data.access_token
const user = await fetch(`${API_BASE}/api/v1/auth/me`, {
  headers: { Authorization: `Bearer ${token}` },
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
    height: 1400,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await client.send('Runtime.evaluate', {
    expression: `localStorage.setItem('crm-auth', ${JSON.stringify(
      JSON.stringify({ state: { token, user: user.data }, version: 0 }),
    )})`,
  })
  await client.send('Page.navigate', { url: `${APP_BASE}${path}` })
  for (let i = 0; i < 80; i += 1) {
    const probe = await client.send('Runtime.evaluate', {
      expression: 'document.body.innerText.trim().length > 200',
      returnByValue: true,
    })
    if (probe.result.value) break
    await sleep(300)
  }
  await sleep(1200)

  for (const label of clicks) {
    const outcome = await client.send('Runtime.evaluate', {
      expression: `(() => {
        const hit = Array.from(document.querySelectorAll('button'))
          .find((n) => (n.innerText || '').trim() === ${JSON.stringify(label)});
        if (!hit) return 'not-found';
        hit.click();
        return 'clicked';
      })()`,
      returnByValue: true,
    })
    console.log(`点击「${label}」→ ${outcome.result.value}`)
    await sleep(800)
  }

  const shot = await client.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: true,
  })
  const file = join(OUT_DIR, outName)
  writeFileSync(file, Buffer.from(shot.data, 'base64'))
  console.log(`✓ ${path} → ${file}`)
  client.close()
} finally {
  browser.kill()
  await sleep(300)
}
