#!/usr/bin/env node
/**
 * 界面冒烟测试：用无头浏览器打开 CRM，注入登录态后逐页截图，并收集控制台报错。
 *
 * 用法：
 *   node ops/smoke_ui.mjs
 *   SMOKE_USER=admin SMOKE_OUT=/tmp/shots node ops/smoke_ui.mjs
 *
 * 为什么要有这个脚本：这个项目要分 6 个阶段做，每做完一段都得确认「页面真的能打开、
 * 数据真的能读出来」，而不是只看构建有没有过。它用浏览器调试协议驱动 Edge/Chrome，
 * 只依赖 Node 内置能力，不额外装 Playwright。
 */

import { spawn } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const EDGE_BIN =
  process.env.EDGE_BIN ?? '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
const APP_BASE = process.env.APP_BASE ?? 'http://localhost:5173'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8000'
const USERNAME = process.env.SMOKE_USER ?? 'admin'
const PASSWORD = process.env.SMOKE_PASSWORD ?? 'admin123'
const CDP_PORT = Number(process.env.CDP_PORT ?? 9333)
const OUT_DIR = process.env.SMOKE_OUT ?? join(tmpdir(), 'crm-smoke')
const PROFILE_DIR = join(tmpdir(), `crm-smoke-profile-${CDP_PORT}`)

/** 取列表接口里真实存在的 id，避免写死 1 号数据（数据变了用例就失效）。 */
async function firstId(path, token) {
  try {
    const response = await fetch(`${API_BASE}/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await response.json()
    const items = body?.data?.items ?? []
    return items.length ? items[0].id : null
  } catch {
    return null
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function apiLogin() {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  })
  const body = await response.json()
  if (body.code !== 0) {
    throw new Error(`登录失败：${body.message}`)
  }
  const token = body.data.access_token
  const meResponse = await fetch(`${API_BASE}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const me = (await meResponse.json()).data
  return { token, user: me }
}

async function waitForCdp() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
      if (response.ok) return
    } catch {
      // 浏览器还没起来，继续等
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
    const listeners = []
    let nextId = 1

    socket.addEventListener('open', () =>
      resolve({
        send(method, params = {}) {
          const id = nextId
          nextId += 1
          return new Promise((res, rej) => {
            pending.set(id, { resolve: res, reject: rej })
            socket.send(JSON.stringify({ id, method, params }))
          })
        },
        on(listener) {
          listeners.push(listener)
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
        const { resolve: res, reject: rej } = pending.get(message.id)
        pending.delete(message.id)
        if (message.error) rej(new Error(JSON.stringify(message.error)))
        else res(message.result)
        return
      }
      for (const listener of listeners) listener(message)
    })
  })
}

async function main() {
  rmSync(PROFILE_DIR, { recursive: true, force: true })
  mkdirSync(OUT_DIR, { recursive: true })

  const auth = await apiLogin()
  console.log(`✓ 接口登录成功：${auth.user.name}（${auth.user.roles.join(',')}）`)

  // 按真实数据组装用例
  const [customerId, opportunityId, productId, quoteId, skuId, orderId] = await Promise.all([
    firstId('/customers', auth.token),
    firstId('/opportunities', auth.token),
    firstId('/products', auth.token),
    firstId('/quotes', auth.token),
    firstId('/pricing/sku-options', auth.token),
    firstId('/orders', auth.token),
  ])
  const PAGES = [
    { path: '/workbench', name: '01-workbench' },
    { path: '/analytics', name: '02-analytics' },
    { path: '/leads', name: '03-leads' },
    { path: '/customers', name: '04-customers' },
    { path: `/customers/${customerId ?? 1}`, name: '05-customer-detail' },
    { path: `/customers/${customerId ?? 1}?tab=files`, name: '05b-customer-files' },
    { path: '/opportunities', name: '06-opportunities' },
    { path: `/opportunities/${opportunityId ?? 1}`, name: '07-opportunity-detail' },
    { path: '/products', name: '08-products' },
    { path: `/products/${productId ?? 1}`, name: '09-product-detail' },
    { path: '/prices', name: '10-price-center' },
    {
      path: `/pricing?sku_id=${skuId ?? 1}&customer_id=${customerId ?? 1}&quantity=3000&quoted_price=25`,
      name: '11-pricing',
    },
    { path: '/quotes', name: '12-quotes' },
    { path: `/quotes/${quoteId ?? 1}`, name: '13-quote-detail' },
    { path: '/approvals', name: '14-approvals' },
    { path: '/orders', name: '15-orders' },
    { path: `/orders/${orderId ?? 1}`, name: '16-order-detail' },
    { path: '/tasks', name: '17-tasks' },
    { path: '/settings', name: '18-settings' },
    { path: '/settings?tab=rules', name: '19-settings-rules' },
    { path: '/agent', name: '20-agent' },
  ]
  console.log(
    `✓ 用例数据：客户 #${customerId} 商机 #${opportunityId} 产品 #${productId} 报价 #${quoteId} 订单 #${orderId}`,
  )

  const browser = spawn(
    EDGE_BIN,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${PROFILE_DIR}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const problems = []
  try {
    await waitForCdp()
    const target = await openTarget(`${APP_BASE}/login`)
    const client = await connect(target.webSocketDebuggerUrl)

    client.on((message) => {
      if (message.method === 'Runtime.exceptionThrown') {
        problems.push(`未捕获异常：${message.params.exceptionDetails.text}`)
      }
      if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        problems.push(
          `控制台报错：${message.params.args.map((a) => a.value ?? a.description).join(' ')}`,
        )
      }
    })

    await client.send('Page.enable')
    await client.send('Runtime.enable')
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    })

    // 注入登录态（与前端 zustand persist 的存储格式一致）
    await client.send('Runtime.evaluate', {
      expression: `localStorage.setItem('crm-auth', ${JSON.stringify(
        JSON.stringify({ state: { token: auth.token, user: auth.user }, version: 0 }),
      )})`,
    })

    for (const page of PAGES) {
      await client.send('Page.navigate', { url: `${APP_BASE}${page.path}` })
      await sleep(2500)
      const shot = await client.send('Page.captureScreenshot', { format: 'png' })
      const file = join(OUT_DIR, `${page.name}.png`)
      writeFileSync(file, Buffer.from(shot.data, 'base64'))

      const text = await client.send('Runtime.evaluate', {
        expression: 'document.body.innerText.slice(0, 120).replace(/\\s+/g, " ")',
        returnByValue: true,
      })
      console.log(`✓ ${page.path} → ${file}`)
      console.log(`  页面首屏文本：${text.result.value}`)
    }

    if (problems.length > 0) {
      console.log('\n发现前端运行时报错：')
      for (const problem of problems) console.log(` - ${problem}`)
      process.exitCode = 1
    } else {
      console.log('\n✓ 未发现控制台报错')
    }
    client.close()
  } finally {
    browser.kill()
    await sleep(300)
    rmSync(PROFILE_DIR, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(`✗ 冒烟测试失败：${error.message}`)
  process.exitCode = 1
})
