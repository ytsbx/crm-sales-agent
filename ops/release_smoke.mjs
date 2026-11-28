#!/usr/bin/env node
/**
 * 发布分支专用冒烟（2026-11-28）。
 *
 * 为什么单独一套：`release/**` 是从生产快照长出来的发布副本，只包含本次要发的
 * 少量改动；拿开发分支的全量回归套件去跑它，必然出现大量「缺少新功能」的红，
 * 这不是发布分支坏了。发布分支真正要守的是三件事：
 *   1. 空库能迁到 head、种子能落库；
 *   2. 后端能启动、登录和核心列表接口可用；
 *   3. 前端能构建，工作台/客户/报价/订单/设置等核心页面能打开并出内容。
 *
 * 全量回归仍然只在功能分支跑，两边职责分开。本脚本不替代全量回归，也不放宽
 * 发布分支自己的业务断言。
 *
 * 用法：
 *   API_BASE=http://127.0.0.1:8000 APP_BASE=http://127.0.0.1:5173 \
 *   EDGE_BIN=/usr/bin/google-chrome node ops/release_smoke.mjs
 */

import { spawn } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const API_BASE = requireEnv('API_BASE')
const APP_BASE = requireEnv('APP_BASE')
const USERNAME = process.env.SMOKE_USER ?? 'admin'
const PASSWORD = process.env.SMOKE_PASSWORD ?? 'admin123'
const CDP_PORT = Number(process.env.CDP_PORT ?? 20000 + (process.pid % 20000))
const PROFILE_DIR = join(tmpdir(), `crm-release-smoke-${CDP_PORT}`)
const EDGE_BIN = resolveBrowser()

const PAGES = [
  { path: '/workbench', name: '工作台', expect: ['工作台', '工作概览'] },
  { path: '/customers', name: '客户中心', expect: ['客户中心'] },
  { path: '/quotes', name: '报价中心', expect: ['报价中心'] },
  { path: '/orders', name: '订单中心', expect: ['订单中心'] },
  { path: '/documents', name: '文档管理', expect: ['文档管理'] },
  { path: '/tasks', name: '销售任务', expect: ['销售任务'] },
  { path: '/settings', name: '系统设置', expect: ['系统设置'] },
]

function requireEnv(name) {
  const value = process.env[name]
  if (!value) throw new Error(`缺少环境变量 ${name}`)
  return value.replace(/\/$/, '')
}

function resolveBrowser() {
  if (process.env.EDGE_BIN) return process.env.EDGE_BIN
  const candidates =
    process.platform === 'darwin'
      ? [
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
          '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        ]
      : process.platform === 'win32'
        ? [
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
          ]
        : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser']
  const found = candidates.find((candidate) => existsSync(candidate))
  if (!found) throw new Error('没找到 Chrome/Edge，请用 EDGE_BIN 指定浏览器')
  return found
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function apiJson(path, token, options = {}) {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  })
  if (!response.ok) throw new Error(`接口 ${path} 返回 HTTP ${response.status}`)
  const body = await response.json()
  if (body.code !== 0) throw new Error(`接口 ${path} 失败：${body.message}`)
  return body.data
}

async function apiLogin() {
  const login = await apiJson('/auth/login', null, {
    method: 'POST',
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  })
  const token = login.access_token
  const user = await apiJson('/auth/me', token)
  return { token, user }
}

async function waitForCdp() {
  for (let i = 0; i < 240; i += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
      if (response.ok) return
    } catch {
      // 继续等浏览器起来
    }
    await sleep(250)
  }
  throw new Error('浏览器调试端口没有就绪')
}

async function openTarget(url) {
  const response = await fetch(
    `http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(url)}`,
    { method: 'PUT' },
  )
  if (!response.ok) throw new Error(`创建标签页失败：${response.status}`)
  return response.json()
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl)
    const pending = new Map()
    let nextId = 1

    socket.addEventListener('open', () =>
      resolve({
        send(method, params = {}) {
          const id = nextId
          nextId += 1
          return new Promise((res, rej) => {
            const timer = setTimeout(() => {
              pending.delete(id)
              rej(new Error(`浏览器命令 ${method} 超过 60 秒未响应`))
            }, 60000)
            pending.set(id, { resolve: res, reject: rej, timer })
            socket.send(JSON.stringify({ id, method, params }))
          })
        },
        close() {
          socket.close()
        },
      }),
    )
    socket.addEventListener('error', () => reject(new Error('无法连接浏览器调试接口')))
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && pending.has(message.id)) {
        const entry = pending.get(message.id)
        pending.delete(message.id)
        clearTimeout(entry.timer)
        if (message.error) entry.reject(new Error(message.error.message))
        else entry.resolve(message.result)
      }
    })
  })
}

async function pageReady(client, path) {
  for (let i = 0; i < 80; i += 1) {
    const probe = await client.send('Runtime.evaluate', {
      expression: `location.pathname === ${JSON.stringify(path)} && document.readyState === 'complete' && Boolean(document.querySelector('#root, #app')?.children.length) && document.body.innerText.trim().length > 100 && !document.body.innerText.includes('页面加载中')`,
      returnByValue: true,
    })
    if (probe.result.value) return
    await sleep(250)
  }
  throw new Error(`页面 ${path} 没有加载完成`)
}

async function expectText(client, path, expected, name) {
  for (let i = 0; i < 80; i += 1) {
    const probe = await client.send('Runtime.evaluate', {
      expression: 'document.body.innerText',
      returnByValue: true,
    })
    const text = probe.result.value ?? ''
    if (expected.some((item) => text.includes(item))) return
    await sleep(250)
  }
  throw new Error(`${name}（${path}）没有出现预期内容：${expected.join(' / ')}`)
}

async function main() {
  console.log(`发布冒烟：API=${API_BASE} APP=${APP_BASE}`)
  const auth = await apiLogin()
  console.log(`✓ 登录成功：${auth.user.name ?? USERNAME}`)

  for (const path of ['/customers?page_size=1', '/quotes?page_size=1', '/orders?page_size=1']) {
    await apiJson(path, auth.token)
  }
  console.log('✓ 核心列表接口可用：客户 / 报价 / 订单')

  rmSync(PROFILE_DIR, { recursive: true, force: true })
  const browser = spawn(
    EDGE_BIN,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-extensions',
      '--disable-sync',
      '--disable-background-networking',
      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]',
      '--disable-dev-shm-usage',
      '--no-sandbox',
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${PROFILE_DIR}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  let client
  try {
    await waitForCdp()
    const target = await openTarget(`${APP_BASE}/login`)
    client = await connect(target.webSocketDebuggerUrl)
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
        JSON.stringify({ state: { token: auth.token, user: auth.user }, version: 0 }),
      )})`,
    })

    await client.send('Page.navigate', { url: `${APP_BASE}/login` })
    await pageReady(client, '/login')

    for (const page of PAGES) {
      await client.send('Page.navigate', { url: `${APP_BASE}${page.path}` })
      await pageReady(client, page.path)
      await expectText(client, page.path, page.expect, page.name)
      console.log(`✓ ${page.name} ${page.path}`)
    }
    console.log('RELEASE_SMOKE_OK')
  } finally {
    if (client) client.close()
    browser.kill('SIGKILL')
    rmSync(PROFILE_DIR, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(`✗ 发布冒烟失败：${error.message}`)
  process.exitCode = 1
})
