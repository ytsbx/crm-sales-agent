#!/usr/bin/env node
/**
 * 界面冒烟测试：用无头浏览器打开 CRM，注入登录态后逐页截图，并收集控制台报错。
 *
 * 用法（必须明确指向本机服务与本机数据库，并关闭外部投递/调度）：
 *   node ops/smoke_ui.mjs
 *   SMOKE_ENABLE_FIXTURES=1 SMOKE_USER=admin SMOKE_OUT=/tmp/shots node ops/smoke_ui.mjs
 *
 * 默认不写报价或订单。只有显式设置 SMOKE_ENABLE_FIXTURES=1 时，才会在**一次性测试库**
 * 中造测试报价和订单；这些业务夹具不会删除，因此不要对开发库或生产库开启此选项。
 * 脚本启动前会检查前后端地址、数据库地址及推送/调度开关。
 *
 * 为什么要有这个脚本：这个项目要分 6 个阶段做，每做完一段都得确认「页面真的能打开、
 * 数据真的能读出来」，而不是只看构建有没有过。它用浏览器调试协议驱动 Edge/Chrome，
 * 只依赖 Node 内置能力，不额外装 Playwright。
 */

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')
const BACKEND_DIR = join(REPO_DIR, 'backend')

/**
 * 找浏览器：优先 EDGE_BIN 环境变量，否则按平台逐个探测常见安装路径。
 * 原来这里只写死 macOS 的 Edge，换到 Windows/Linux 上会直接崩，
 * 所以改成「探测第一个存在的」。
 */
function resolveBrowser() {
  if (process.env.EDGE_BIN) return process.env.EDGE_BIN
  const candidates =
    process.platform === 'win32'
      ? [
          'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
          'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
          join(process.env.LOCALAPPDATA ?? '', 'Microsoft\\Edge\\Application\\msedge.exe'),
          'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
          'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        ]
      : process.platform === 'darwin'
        ? [
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
          ]
        : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium']
  const hit = candidates.find((path) => path && existsSync(path))
  if (!hit) {
    throw new Error(
      '找不到 Edge/Chrome。请设置环境变量 EDGE_BIN 指向浏览器可执行文件，例如：\n' +
        '  $env:EDGE_BIN="C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"',
    )
  }
  return hit
}

const EDGE_BIN = resolveBrowser()
const APP_BASE = process.env.APP_BASE ?? 'http://localhost:5173'
const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8000'
const USERNAME = process.env.SMOKE_USER ?? 'admin'
const PASSWORD = process.env.SMOKE_PASSWORD ?? 'admin123'
const CDP_PORT = Number(process.env.CDP_PORT ?? 9333)
const OUT_DIR = process.env.SMOKE_OUT ?? join(tmpdir(), 'crm-smoke')
const PROFILE_DIR = join(tmpdir(), `crm-smoke-profile-${CDP_PORT}`)
const FIXTURES_ENABLED = process.env.SMOKE_ENABLE_FIXTURES === '1'

function assertSafeTargets() {
  const isLoopback = (value, label) => {
    let hostname
    try {
      hostname = new URL(value).hostname.replace(/^\[|\]$/g, '')
    } catch {
      throw new Error(`${label} 不是有效 URL：${value}`)
    }
    if (!['localhost', '127.0.0.1', '::1'].includes(hostname)) {
      throw new Error(`${label} 必须指向本机回环地址，当前为 ${hostname}`)
    }
  }

  isLoopback(API_BASE, 'API_BASE')
  isLoopback(APP_BASE, 'APP_BASE')

  const dbUrl = process.env.DATABASE_URL
  if (!dbUrl) throw new Error('必须显式设置 DATABASE_URL，避免清理脚本误连 backend/.env 中的数据库')
  const match = dbUrl.match(/^[^:]+:\/\/(?:[^@/]+@)?([^/?]*)(?:\/[^?]*)?(?:\?(.*))?$/)
  if (!match) throw new Error('DATABASE_URL 格式无法安全识别')
  const rawHost = match[1]
  const host = rawHost.startsWith('[')
    ? rawHost.slice(1, rawHost.indexOf(']'))
    : rawHost.split(':')[0]
  const socketHost = new URLSearchParams(match[2] ?? '').get('host')
  if (!['localhost', '127.0.0.1', '::1'].includes(host) && !(host === '' && socketHost?.startsWith('/'))) {
    throw new Error('DATABASE_URL 必须指向本机回环地址或本机 Unix socket')
  }

  const enabled = (name) => ['1', 'true'].includes(String(process.env[name] ?? '').toLowerCase())
  const disabled = (name) => ['0', 'false'].includes(String(process.env[name] ?? '').toLowerCase())
  if (!enabled('DINGTALK_PUSH_OFF') || !enabled('WECOM_PUSH_OFF')) {
    throw new Error('UI 冒烟要求 DINGTALK_PUSH_OFF=1 和 WECOM_PUSH_OFF=1')
  }
  if (!disabled('SCHEDULER_ENABLED')) throw new Error('UI 冒烟要求 SCHEDULER_ENABLED=false')
  if (process.env.DEEPSEEK_API_KEY) throw new Error('UI 冒烟期间必须清空 DEEPSEEK_API_KEY')
  if (FIXTURES_ENABLED && !dbUrl.toLowerCase().includes('test') && !dbUrl.toLowerCase().includes('smoke')) {
    throw new Error('SMOKE_ENABLE_FIXTURES=1 仅允许用于数据库名称含 test 或 smoke 的一次性测试库')
  }
}

/**
 * 找后端用的 Python：优先项目自己的 venv，其次 PATH 上的 python3/python。
 * CI 的 UI 冒烟 job 是把依赖装进系统 python 再起后端的，所以没有 venv 时
 * 直接退回 PATH——两边都能跑。
 */
function resolvePython() {
  const candidates =
    process.platform === 'win32'
      ? [join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')]
      : [join(BACKEND_DIR, '.venv', 'bin', 'python')]
  const venv = candidates.find((path) => existsSync(path))
  return venv ?? (process.platform === 'win32' ? 'python' : 'python3')
}

/**
 * 收尾：把本轮冒烟跑出来的站内通知/系统留痕清掉。
 *
 * 冒烟会走真实业务流程（建报价、提审批、建打样），这些动作会往账号里写
 * 站内通知；以前跑完就留在那儿，下次谁登录谁看见。复用后端同一个清理脚本
 * （`clean_test_run_leftovers.py`），**按本轮时间窗**清，窗口之前的一律不动。
 *
 * 清理失败要算失败（否则残留会一直悄悄攒）；但找不到 Python / 脚本时只提示，
 * 不算失败——那是环境没跑后端的情况，不是"留下了垃圾"。
 */
function cleanTestResidue(startedAt) {
  const script = join(BACKEND_DIR, 'scripts', 'clean_test_run_leftovers.py')
  if (!existsSync(script)) {
    console.log(`（提示：找不到 ${script}，跳过收尾清扫）`)
    return
  }
  const result = spawnSync(resolvePython(), ['scripts/clean_test_run_leftovers.py'], {
    cwd: BACKEND_DIR,
    env: { ...process.env, TEST_RUN_STARTED_AT: startedAt, PYTHONPATH: '.' },
    encoding: 'utf8',
  })
  if (result.error || result.status !== 0) {
    console.log(
      `✗ 收尾清扫失败（本轮可能留下站内通知）：${result.error?.message ?? result.stderr?.trim() ?? result.status}`,
    )
    process.exitCode = 1
    return
  }
  const line = (result.stdout ?? '').trim().split('\n').filter(Boolean).pop() ?? ''
  console.log(`✓ 收尾清扫：${line}`)
}

/** 取列表接口里真实存在的记录，避免写死演示数据 id。 */
async function firstRecord(path, token) {
  try {
    const response = await fetch(`${API_BASE}/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await response.json()
    const items = Array.isArray(body?.data) ? body.data : (body?.data?.items ?? [])
    return items.length ? items[0] : null
  } catch {
    return null
  }
}

async function firstId(path, token) {
  return (await firstRecord(path, token))?.id ?? null
}

async function checkedJson(path, token, options = {}) {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  })
  const body = await response.json()
  if (!response.ok || body?.code !== 0) {
    throw new Error(`${options.method ?? 'GET'} ${path}: ${body?.message ?? response.status}`)
  }
  return body.data
}

/**
 * 报价中心为空时临时造一张报价（带 1 条明细），否则 What-if 面板只能截到空态。
 * 返回新报价的 id；失败返回 null，由调用方跳过依赖它的用例。
 */
async function createSeedQuote(token, customerId) {
  if (!customerId) return null
  try {
    const headers = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    }
    // 报价必须挂商机（后端口径），所以先找一张现成商机；一张都没有就用传入的
    // 客户建一张快捷商机。以前这里只传 customer_id，接口直接 40001，
    // "报价中心为空时自动造一张"的兜底其实早就失效了。
    let opportunityId = await firstId('/opportunities?page_size=1', token)
    if (!opportunityId) {
      const opp = await fetch(`${API_BASE}/api/v1/opportunities`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ customer_id: customerId, title: '冒烟用例快捷商机' }),
      }).then((r) => r.json())
      opportunityId = opp?.data?.id ?? null
    }
    if (!opportunityId) return null
    const created = await fetch(`${API_BASE}/api/v1/quotes`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ opportunity_id: opportunityId }),
    }).then((r) => r.json())
    const quoteId = created?.data?.quote_id
    const versionId = created?.data?.version_id
    if (!quoteId || !versionId) return null
    const skus = await fetch(`${API_BASE}/api/v1/pricing/sku-options`, { headers }).then((r) =>
      r.json(),
    )
    const skuId = skus?.data?.[0]?.id
    if (skuId) {
      await fetch(`${API_BASE}/api/v1/quote-versions/${versionId}/items/batch`, {
        method: 'POST',
        headers,
        body: JSON.stringify([{ sku_id: skuId, quantity: 3000, quoted_price: 28 }]),
      })
    }
    return quoteId
  } catch {
    return null
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * 订单中心为空时在测试数据中造一张订单，覆盖真实的订单详情页。
 * 返回错误原因供主流程判失败，避免静默退回 /orders/1 后仍报冒烟通过。
 */
async function createOrderIfEmpty(token, customerId) {
  try {
    if (!customerId) throw new Error('没有可用于建单的客户')

    let opportunityId = await firstId('/opportunities?page_size=1', token)
    const skuId = await firstId('/pricing/sku-options', token)
    if (!skuId) throw new Error('没有可用于建单的 SKU')
    const priceResult = await checkedJson('/pricing/calculate', token, {
      method: 'POST',
      body: JSON.stringify({ sku_id: skuId, quantity: 100, customer_id: customerId }),
    })
    const quotePrice = Number(priceResult?.recommended_price)
    if (!Number.isFinite(quotePrice) || quotePrice <= 0) {
      throw new Error('核价接口未返回有效的建议价，不能创建测试订单')
    }

    if (!opportunityId) {
      const opportunity = await checkedJson('/opportunities', token, {
        method: 'POST',
        body: JSON.stringify({ customer_id: customerId, title: 'UI 冒烟测试商机' }),
      })
      opportunityId = opportunity?.id
      if (!opportunityId) throw new Error('创建测试商机没有返回 id')
      await checkedJson(`/opportunities/${opportunityId}/items`, token, {
        method: 'POST',
        body: JSON.stringify({ sku_id: skuId, quantity: 100, target_price: quotePrice }),
      })
    }

    // 报价必须关联商机；使用同一 SKU 的系统建议价，避免凭空编造价格或触发低价审批。
    const created = await checkedJson('/quotes', token, {
      method: 'POST',
      body: JSON.stringify({ opportunity_id: opportunityId }),
    })
    const quoteId = created?.quote_id
    const versionId = created?.version_id
    if (!quoteId || !versionId) throw new Error('创建测试报价未返回报价或版本 id')

    await checkedJson(`/quote-versions/${versionId}/items/batch`, token, {
      method: 'POST',
      body: JSON.stringify([{ sku_id: skuId, quantity: 100, quoted_price: quotePrice }]),
    })
    const submitted = await checkedJson(`/quote-versions/${versionId}/submit-approval`, token, {
      method: 'POST',
      body: JSON.stringify({ reason: '仅用于本地 UI 冒烟' }),
    })
    if (submitted?.version?.approval_status !== 'approved') {
      throw new Error(`测试报价未自动通过审批（状态 ${submitted?.version?.approval_status ?? '未知'}）`)
    }

    // 在一次性数据库内登记虚构的正式发送事实，再模拟客户接受；不调用外部服务。
    await checkedJson(`/quote-versions/${versionId}/mark-sent`, token, {
      method: 'POST', body: JSON.stringify({ channel: '本地验收', receiver: '虚构客户' }),
    })
    await checkedJson(`/quote-versions/${versionId}/accept`, token, { method: 'POST' })
    const converted = await checkedJson(`/quote-versions/${versionId}/convert-to-order`, token, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    if (!converted?.order_id) throw new Error('报价转订单没有返回订单 id')
    return { orderId: converted.order_id, quoteId, opportunityId }
  } catch (error) {
    return { orderId: null, quoteId: null, opportunityId: null, error: error.message }
  }
}

/** 三类客户过程事实及原单入口，仅用于显式启用夹具的隔离库。 */
async function createTimelineFixture(token, customerId, skuId) {
  const post = (path, body) => checkedJson(path, token, {
    method: 'POST', body: JSON.stringify(body),
  })
  const { order_id: orderId } = await post('/orders', {
    customer_id: customerId, delivery_date: '2026-12-01',
    items: [{ sku_id: skuId, quantity: 10, unit_price: 100 }],
  })
  await checkedJson(`/orders/${orderId}/milestones`, token)
  const change = await post(`/orders/${orderId}/schedule-changes`, {
    delivery_kind: 'shipping', new_delivery_date: '2026-12-08', reason: '客户时间线 UI 冒烟',
  })
  await post(`/orders/${orderId}/schedule-changes/${change.id}/confirm`, {})
  const items = await checkedJson(`/orders/${orderId}/items`, token)
  const batch = await post(`/orders/${orderId}/shipments`, {
    items: [{ order_item_id: items[0].id, planned_qty: 10 }],
  })
  await post(`/orders/${orderId}/shipments/${batch.batch_id}/ship`, { actual_ship_date: '2026-10-05' })
  const plan = await post(`/orders/${orderId}/receivables`, {
    plan_name: 'UI 时间线应收', due_date: '2026-12-08', amount: 1000,
  })
  const payment = await post('/payments', {
    receivable_plan_id: plan.id, received_date: '2026-10-05', received_amount: 100,
  })
  await post(`/payments/${payment.id}/confirm`, {})
  const sample = await post('/samples', {
    customer_id: customerId, items: [{ sku_id: skuId, quantity: 1 }],
  })
  await post(`/samples/${sample.id}/approve`, { approved: true })
  await post(`/samples/${sample.id}/made`, {})
  await post(`/samples/${sample.id}/ship`, { carrier: 'UI 测试物流', tracking_no: 'UI-SAMPLE' })
  await post(`/samples/${sample.id}/sign`, {})
  await post(`/samples/${sample.id}/confirm`, { accepted: true, remark: '客户时间线 UI 验证' })
  return { orderId, sampleId: sample.id }
}

/**
 * 按可见文字点击页面元素。
 *
 * 有些交付物（商机看板、Copilot 抽屉）默认不出现，只有点一下才渲染出来；
 * 只截图列表页等于没验证。这里按文字找元素再派发真实点击事件。
 */
async function clickByText(client, text, { tag = 'button' } = {}) {
  const expression = `(() => {
    const nodes = Array.from(document.querySelectorAll(${JSON.stringify(tag)}));
    const hit = nodes.find((node) => (node.innerText || '').trim() === ${JSON.stringify(text)});
    if (!hit) return 'not-found';
    hit.click();
    return 'clicked';
  })()`
  const result = await client.send('Runtime.evaluate', { expression, returnByValue: true })
  return result.result.value
}

/** 等某个文字在页面上出现（交互后用它确认结果真的渲染了）。 */
async function waitForText(client, text, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const result = await client.send('Runtime.evaluate', {
      expression: `document.body.innerText.includes(${JSON.stringify(text)})`,
      returnByValue: true,
    })
    if (result.result.value) return true
    await sleep(250)
  }
  return false
}

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
  assertSafeTargets()
  rmSync(PROFILE_DIR, { recursive: true, force: true })
  mkdirSync(OUT_DIR, { recursive: true })
  // 收尾清扫的时间窗起点：本脚本之前产生的通知一律不动
  const runStartedAt = new Date().toISOString()

  const auth = await apiLogin()
  console.log(`✓ 接口登录成功：${auth.user.name}（${auth.user.roles.join(',')}）`)
  // 按真实数据组装用例
  const [customer, opportunity, product, quote, sku, firstOrder, sample] = await Promise.all([
    firstRecord('/customers', auth.token),
    firstRecord('/opportunities', auth.token),
    firstRecord('/products', auth.token),
    firstRecord('/quotes', auth.token),
    firstRecord('/pricing/sku-options', auth.token),
    firstRecord('/orders', auth.token),
    firstRecord('/samples', auth.token),
  ])
  const customerId = customer?.id ?? null
  let opportunityId = opportunity?.id ?? null
  const productId = product?.id ?? null
  const skuId = sku?.id ?? null
  let quoteId = quote?.id ?? null
  let orderId = firstOrder?.id ?? null
  const setupProblems = []

  // 订单中心为空时必须成功造单；失败要让 UI 冒烟失败，不能静默退回 /orders/1。
  if (!orderId) {
    if (!FIXTURES_ENABLED) {
      setupProblems.push('订单为空；仅在一次性测试库显式设置 SMOKE_ENABLE_FIXTURES=1 后才会创建测试订单')
    } else {
      const created = await createOrderIfEmpty(auth.token, customerId)
      orderId = created.orderId
      quoteId ??= created.quoteId
      opportunityId ??= created.opportunityId
      if (orderId) console.log(`✓ 订单中心为空，已在测试库造出订单 #${orderId}`)
      else setupProblems.push(`自动建单失败：${created.error}`)
    }
  }
  if (!quoteId) {
    if (!FIXTURES_ENABLED) {
      setupProblems.push('没有报价；仅在一次性测试库显式设置 SMOKE_ENABLE_FIXTURES=1 后才会创建测试报价')
    } else {
      quoteId = await createSeedQuote(auth.token, customerId)
      if (!quoteId) setupProblems.push('没有可用于报价详情/What-if 验收的报价')
    }
  }
  if (!customerId) setupProblems.push('没有可用于客户详情验收的客户')
  if (!opportunityId) setupProblems.push('没有可用于商机详情验收的商机')
  if (!productId) setupProblems.push('没有可用于产品详情验收的产品')
  if (!skuId) setupProblems.push('没有可用于核价/物流验收的 SKU')
  if (!orderId) setupProblems.push('没有可用于订单详情验收的订单')
  const PAGES = [
    { path: '/workbench', name: '01-workbench', expectText: '今日待办' },
    { path: '/analytics', name: '02-analytics', expectText: '数据分析' },
    { path: '/leads', name: '03-leads', expectText: '线索中心' },
    { path: '/customers', name: '04-customers', expectText: '客户中心' },
    // 撞单裁定（验收20）：系统摆证据、归属由人裁定
    { path: '/duplicate-cases', name: '04b-duplicate-cases', expectText: '撞单裁定' },
    { path: `/customers/${customerId ?? 1}`, name: '05-customer-detail', expectText: customer?.name ?? '客户不存在' },
    { path: `/customers/${customerId ?? 1}?tab=files`, name: '05b-customer-files', expectText: '合同、图纸、回款凭证' },
    { path: '/opportunities', name: '06-opportunities', expectText: '商机中心' },
    { path: `/opportunities/${opportunityId ?? 1}`, name: '07-opportunity-detail', expectText: opportunity?.title ?? '商机不存在' },
    { path: '/products', name: '08-products', expectText: '产品中心' },
    { path: `/products/${productId ?? 1}`, name: '09-product-detail', expectText: product?.name ?? '产品不存在' },
    { path: '/prices', name: '10-price-center', expectText: '价格中心' },
    {
      path: `/pricing?sku_id=${skuId ?? 1}&customer_id=${customerId ?? 1}&quantity=3000&quoted_price=25`,
      name: '11-pricing',
      expectText: '核价',
    },
    { path: '/quotes', name: '12-quotes', expectText: '报价中心' },
    { path: `/quotes/${quoteId ?? 1}`, name: '13-quote-detail', expectText: '版本与方案对比' },
    { path: '/approvals', name: '14-approvals', expectText: '报价审批' },
    { path: '/orders', name: '15-orders', expectText: '订单中心' },
    { path: `/orders/${orderId ?? 1}`, name: '16-order-detail', expectText: '未推送 ERP/MES' },
    { path: '/tasks', name: '17-tasks', expectText: '销售任务' },
    { path: '/settings', name: '18-settings', expectText: '系统设置' },
    { path: '/settings?tab=rules', name: '19-settings-rules', expectText: '业务规则' },
    { path: '/settings?tab=tags', name: '23-settings-tags', expectText: '客户标签' },
    { path: '/settings?tab=notifications', name: '31-settings-notifications', expectText: '通知' },
    { path: '/settings?tab=roles', name: '24-settings-roles', expectText: '角色与数据范围' },
    { path: '/settings?tab=departments', name: '25-settings-departments', expectText: '部门' },
    { path: '/agent', name: '20-agent', expectText: '新建会话' },
    // 定制需求（原菜单名「知识库」）：需求列表 + 修订链/钉钉审批入口都在这一页
    { path: '/inquiries', name: '32-inquiries', expectText: '定制需求' },
    { path: '/samples', name: '22-samples', expectText: '样品管理' },
    // 深链详情：/samples/:id 直接打开该条详情抽屉（案例证据跳转也走它）
    ...(sample ? [{ path: `/samples/${sample.id}`, name: '22b-sample-detail', expectText: '样品' }] : []),
    { path: '/wecom', name: '30-wecom', expectText: '企业微信' },
    {
      // 带参数进入，才能真正验证"选了 SKU 能出计费重与方案"，
      // 否则只截图到空状态，等于没验证结果区
      path: `/logistics?sku_id=${skuId ?? 1}&quantity=3000&destination=%E5%8D%8E%E4%B8%9C`,
      name: '21-logistics',
      expectText: '物流',
    },
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
      // CI 容器 /dev/shm 很小，Chrome 标签页会随机崩（白屏 + "Unable to
      // capture screenshot"），这两个是官方建议的容器稳定参数
      '--disable-dev-shm-usage',
      '--no-sandbox',
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${PROFILE_DIR}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const problems = [...setupProblems]
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

    // 预热：Vite 首次启动会在后台做依赖预构建（bundling dependencies），
    // 这期间页面是空白的。先访问一次等它就绪，否则头几张截图会拍到白屏，
    // 看起来像"页面打不开"，其实是构建窗口期（Windows 上尤其明显）。
    await client.send('Page.navigate', { url: `${APP_BASE}/login` })
    for (let i = 0; i < 120; i += 1) {
      const probe = await client.send('Runtime.evaluate', {
        expression: 'Boolean(document.querySelector("#root, #app")?.children.length)',
        returnByValue: true,
      })
      if (probe.result.value) break
      await sleep(500)
    }
    console.log('✓ 前端已就绪（依赖预构建完成）')

    // 预热二遍跑：逐页无断言过一遍，把懒加载路由块编译掉、把 vite 的
    // 依赖再优化（会触发整页自动刷新）全部提前耗尽。CI 冷环境上，
    // 预优化会在正式用例的探测窗口里把页面打成白屏（控制台还被清空），
    // 本地暖环境复现不了——白白背一个"页面打不开"的假警报。
    console.log('—— 预热：无断言过一遍全部页面（触发懒编译/依赖再优化）——')
    for (const page of (process.env.SMOKE_FOLLOWUP_ONLY === '1' || process.env.SMOKE_RULES_ONLY === '1' || process.env.SMOKE_QUOTE_ONLY === '1' || process.env.SMOKE_COPILOT_ONLY === '1' || process.env.SMOKE_DEMAND_ONLY === '1' || process.env.SMOKE_SAMPLE_SOURCE_ONLY === '1' || process.env.SMOKE_ORDER_DRAFT_ONLY === '1' ? [] : PAGES)) {
      await client.send('Page.navigate', { url: `${APP_BASE}${page.path}` })
      for (let i = 0; i < 24; i += 1) {
        const probe = await client.send('Runtime.evaluate', {
          expression: 'Boolean(document.querySelector("#root, #app")?.children.length)',
          returnByValue: true,
        })
        if (probe.result.value) break
        await sleep(500)
      }
    }
    await sleep(2500) // 等 vite 依赖再优化与其触发的整页刷新彻底安定
    console.log('✓ 预热完成')

    for (const page of (process.env.SMOKE_FOLLOWUP_ONLY === '1' || process.env.SMOKE_RULES_ONLY === '1' || process.env.SMOKE_QUOTE_ONLY === '1' || process.env.SMOKE_COPILOT_ONLY === '1' || process.env.SMOKE_DEMAND_ONLY === '1' || process.env.SMOKE_SAMPLE_SOURCE_ONLY === '1' || process.env.SMOKE_ORDER_DRAFT_ONLY === '1' ? [] : PAGES)) {
      await client.send('Page.navigate', { url: `${APP_BASE}${page.path}` })
      // 等页面真的渲染出来（而不是固定 sleep），最多 15 秒
      let rendered = false
      for (let i = 0; i < 60; i += 1) {
        const probe = await client.send('Runtime.evaluate', {
          expression:
            'Boolean(document.querySelector("#root, #app")?.children.length) && Boolean(document.querySelector(".page-container")?.innerText.trim().length)',
          returnByValue: true,
        })
        if (probe.result.value) {
          rendered = true
          break
        }
        await sleep(250)
      }
      await sleep(400) // 给 react-query 的数据渲染留一点时间
      const shot = await client.send('Page.captureScreenshot', { format: 'png' })
      const file = join(OUT_DIR, `${page.name}.png`)
      writeFileSync(file, Buffer.from(shot.data, 'base64'))

      const text = await client.send('Runtime.evaluate', {
        expression: 'document.querySelector(".page-container")?.innerText.replace(/\\s+/g, " ") ?? ""',
        returnByValue: true,
      })
      if (!rendered) {
        // 失败现场自动转储：光说"没渲染"排查不了，把 URL 和 DOM 头部带回来
        const diag = await client.send('Runtime.evaluate', {
          expression:
            'JSON.stringify({ url: location.href, html: document.body.innerHTML.slice(0, 800) })',
          returnByValue: true,
        })
        problems.push(`页面始终没有渲染出内容：${page.path} → ${diag.result.value}`)
      }
      const pageText = text.result.value ?? ''
      if (page.expectText && !pageText.includes(page.expectText)) {
        // 探测完整内容，日志只打印短片段，便于失败后定位且不刷屏。
        const content = await client.send('Runtime.evaluate', {
          expression: 'document.querySelector(".page-container")?.innerText ?? ""',
          returnByValue: true,
        })
        problems.push(
          `页面主内容缺少「${page.expectText}」：${page.path} → ${(content.result.value ?? '').slice(0, 400).replace(/\\s+/g, ' ')}`,
        )
      }
      console.log(`${rendered && (!page.expectText || pageText.includes(page.expectText)) ? '✓' : '✗'} ${page.path} → ${file}`)
      console.log(`  页面主内容：${pageText.slice(0, 160)}`)
    }

    // ---- 需要点击才出现的交付物：看板 / Copilot 抽屉 / What-if ----------------
    // 这几项是"交互后才有"的，逐页截图覆盖不到，所以单独点一遍。
    // 依赖报价数据的用例在没有报价时先造一张，否则会拿 404 页面当"通过"。
    let interactionQuoteId = quoteId
    if (!interactionQuoteId) {
      interactionQuoteId = await createSeedQuote(auth.token, customerId)
      console.log(
        interactionQuoteId
          ? `✓ 报价中心是空的，已临时造一张报价 #${interactionQuoteId} 用于验证 What-if`
          : '（提示：造报价失败，跳过 What-if 用例）',
      )
    }

    const timelineFixture = FIXTURES_ENABLED
      ? await createTimelineFixture(auth.token, customerId, skuId)
      : null
    const INTERACTIONS = [
      {
        name: '26-opportunity-board',
        path: '/opportunities',
        clicks: ['看板'],
        expect: ['新询盘', '暂无商机', '推进'],
      },
      {
        name: '27-copilot-drawer',
        path: '/workbench',
        clicks: ['AI 助手'],
        expect: ['Sales Copilot', '当前上下文'],
      },
      {
        name: '28-quote-whatif',
        path: `/quotes/${interactionQuoteId ?? 1}`,
        clicks: [],
        expect: ['版本与方案对比', '边际测算'],
        skip: !interactionQuoteId,
      },
      {
        name: '29-quote-copilot-context',
        path: `/quotes/${interactionQuoteId ?? 1}`,
        clicks: ['AI 助手'],
        expect: ['当前上下文：报价单'],
        skip: !interactionQuoteId,
      },
      {
        // 批量录入整版明细（只有"未提交、未发送"的草稿版本才有这个按钮；
        // 当前这张报价不是草稿时跳过，不算功能缺失）
        name: '33-quote-batch-items',
        path: `/quotes/${interactionQuoteId ?? 1}`,
        clicks: ['批量录入'],
        expect: ['批量录入明细', '整版替换'],
        optional: true,
        skip: !interactionQuoteId,
      },
    ]

    if (timelineFixture) INTERACTIONS.push({
      name: '34-customer-timeline', path: `/customers/${customerId}?tab=logs`, clicks: [],
      expect: ['已由财务确认', '交期变更', '已实发', '查看原单'], expectAll: true,
      sourcePath: `/orders/${timelineFixture.orderId}`,
    })

    if (timelineFixture) INTERACTIONS.push({
      name: '35-customer-sample-source', path: `/customers/${customerId}?tab=logs`, clicks: [],
      expect: ['制作完成', '已签收', '客户确认：接受', '查看原单'], expectAll: true,
      sourcePath: `/samples/${timelineFixture.sampleId}`, sourceText: `样品申请 #${timelineFixture.sampleId}`,
    })

    if (FIXTURES_ENABLED) INTERACTIONS.push({
      name: '36-followup-required-plan', path: `/customers/${customerId}?tab=followups`,
      clicks: ['记录跟进'], expect: ['下一步动作 *', '下次跟进时间 *', '保存后自动生成后续待办。'],
      expectAll: true, followupPlan: true,
    }, {
      name: '37-followup-exemption', path: `/customers/${customerId}?tab=followups`,
      clicks: ['记录跟进'], expect: ['CHKUI免填跟进'], expectAll: true, followupExemption: true,
    }, {
      name: '38-followup-scheduled', path: `/customers/${customerId}?tab=followups`,
      clicks: ['记录跟进'], expect: ['CHKUI计划跟进', 'CHKUI回访客户'], expectAll: true, followupSchedule: true,
    })

    if (FIXTURES_ENABLED) INTERACTIONS.push({
      name: '39-settings-rule-save', path: '/settings?tab=rules', clicks: [],
      expect: ['自动任务规则', '业务规则'], expectAll: true, ruleSave: true,
    })

    if (FIXTURES_ENABLED) {
      const lifecycleId = await createSeedQuote(auth.token, customerId)
      if (!lifecycleId) throw new Error('报价流程验收未创建测试报价')
      const versions = await checkedJson(`/quotes/${lifecycleId}/versions`, auth.token)
      const versionId = versions[0].id
      const submitted = await checkedJson(`/quote-versions/${versionId}/submit-approval`, auth.token, {
        method: 'POST', body: JSON.stringify({ reason: '虚构 UI 报价流程' }),
      })
      if (submitted.version.approval_status !== 'approved') throw new Error('报价流程 UI 夹具未通过审批')
      INTERACTIONS.push({
        name: '40-quote-customer-response', path: `/quotes/${lifecycleId}`, clicks: [],
        expect: ['客户拒绝'], expectAll: true, quoteLifecycle: { quoteId: lifecycleId, versionId },
      })
    }

    if (FIXTURES_ENABLED && (auth.user.roles.includes('admin') || auth.user.permissions.includes('agent:use'))) {
      INTERACTIONS.push({
        name: '42-workbench-copilot-analysis', path: '/workbench', clicks: [],
        expect: ['Sales Copilot', '当前上下文：工作台', '分析本月成交冲刺机会'],
        expectAll: true, workbenchAnalysis: true,
      })
    }

    if (FIXTURES_ENABLED && auth.user.roles.includes('admin')) {
      const demand = await checkedJson('/opportunities', auth.token, {
        method: 'POST', body: JSON.stringify({ title: 'CHKUI独立采购需求', customer_id: customerId }),
      })
      INTERACTIONS.push({
        name: '44-opportunity-demand-records', path: `/opportunities/${demand.id}`, clicks: [],
        expect: ['关联单据', '定制询价及修订', '报价', '打样', '订单'], expectAll: true,
        demandFlow: { opportunityId: demand.id, customerId },
      })
    }

    if (FIXTURES_ENABLED && auth.user.roles.includes('admin')) {
      const inquiry = await checkedJson('/custom-inquiries', auth.token, {
        method: 'POST', body: JSON.stringify({ title: 'CHKUI来源采购', customer_id: customerId, quantity: 10000, description: '原规格' }),
      })
      const quote = await checkedJson(`/custom-inquiries/${inquiry.id}/create-quote`, auth.token, {
        method: 'POST', body: JSON.stringify({ unit_cost: 10, quoted_price: 20 }),
      })
      INTERACTIONS.push({ name: '48-sample-source-quantities', path: `/quotes/${quote.quote_id}`, clicks: [],
        expect: ['原采购数量', '本次样品数量', '打样来源'], expectAll: true,
        sampleSource: { ...quote, inquiryId: inquiry.id, opportunityId: quote.opportunity_id },
      })
    }

    if (FIXTURES_ENABLED && auth.user.roles.includes('admin')) {
      const inquiry = await checkedJson('/custom-inquiries', auth.token, { method: 'POST', body: JSON.stringify({ title: 'CHKUI订单准备', customer_id: customerId, quantity: 10000, description: '订单原规格' }) })
      const quote = await checkedJson(`/custom-inquiries/${inquiry.id}/create-quote`, auth.token, { method: 'POST', body: JSON.stringify({ unit_cost: 10, quoted_price: 20 }) })
      INTERACTIONS.push({ name: '50-order-draft-flow', path: `/quotes/${quote.quote_id}`, clicks: [], expect: ['订单详情'], orderDraft: { ...quote, inquiryId: inquiry.id } })
    }

    for (const item of (process.env.SMOKE_ORDER_DRAFT_ONLY === '1' ? INTERACTIONS.filter((item) => item.orderDraft) : process.env.SMOKE_SAMPLE_SOURCE_ONLY === '1' ? INTERACTIONS.filter((item) => item.sampleSource) : process.env.SMOKE_DEMAND_ONLY === '1' ? INTERACTIONS.filter((item) => item.demandFlow) : process.env.SMOKE_COPILOT_ONLY === '1' ? INTERACTIONS.filter((item) => item.workbenchAnalysis) : process.env.SMOKE_QUOTE_ONLY === '1' ? INTERACTIONS.filter((item) => item.quoteLifecycle) : process.env.SMOKE_RULES_ONLY === '1' ? INTERACTIONS.filter((item) => item.ruleSave) : process.env.SMOKE_FOLLOWUP_ONLY === '1' ? INTERACTIONS.filter((item) => item.followupPlan || item.followupExemption || item.followupSchedule) : INTERACTIONS)) {
      if (item.skip) {
        console.log(`- 跳过 ${item.name}（缺少赖以验证的数据）`)
        continue
      }
      await client.send('Page.navigate', { url: `${APP_BASE}${item.path}` })
      let ok = false
      for (let i = 0; i < 60; i += 1) {
        const probe = await client.send('Runtime.evaluate', {
          expression:
            'Boolean(document.querySelector("#root, #app")?.children.length) && document.body.innerText.trim().length > 0',
          returnByValue: true,
        })
        if (probe.result.value) {
          ok = true
          break
        }
        await sleep(250)
      }
      await sleep(600)

      let optionalSkip = false
      for (const label of item.clicks) {
        const outcome = await clickByText(client, label)
        if (outcome !== 'clicked') {
          if (item.optional) {
            // 可选用例：前置条件不满足（比如这张报价不是草稿）就跳过，不算失败
            console.log(`- 跳过 ${item.name}（点不到「${label}」：当前数据不满足前置条件）`)
            optionalSkip = true
            break
          }
          const diag = await client.send('Runtime.evaluate', {
            expression:
              'JSON.stringify({ url: location.href, html: document.body.innerHTML.slice(0, 800) })',
            returnByValue: true,
          })
          problems.push(`点不到「${label}」（${item.path}） → ${diag.result.value}`)
        }
        await sleep(500)
      }
      if (optionalSkip) continue

      if (item.orderDraft) {
        if (!await waitForText(client, '按此版本建订单草稿')) throw new Error('报价缺少草稿入口')
        await clickByText(client, '按此版本建订单草稿')
        if (!await waitForText(client, '本次下单数量：')) throw new Error('建草稿弹窗未打开')
        const quantity = await client.send('Runtime.evaluate', { expression: `document.querySelector('.semi-modal-content .semi-input-number input')?.value`, returnByValue: true })
        if (Number(quantity.result.value) !== 10000) throw new Error('订单草稿数量没有沿用来源')
        await clickByText(client, '建立订单草稿')
        if (!await waitForText(client, '确认正式下单')) throw new Error('订单草稿详情未打开')
        const drafts = await checkedJson(`/order-drafts?opportunity_id=${item.orderDraft.opportunity_id}`, auth.token)
        const draft = drafts.items.find(row => row.source_context.id === item.orderDraft.version_id)
        if (!draft) throw new Error('草稿未关联商机')
        if ((await checkedJson(`/orders?opportunity_id=${item.orderDraft.opportunity_id}`, auth.token)).total !== 0) throw new Error('创建草稿时错误生成正式订单')
        const shot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '50-order-draft-detail.png'), Buffer.from(shot.data, 'base64'))
        const setQuantity = async value => {
          const result=await client.send('Runtime.evaluate', { expression: `(() => { const input=document.querySelector('.page-container .semi-input-number input'); if(!input) return false; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input, ${JSON.stringify(''+value)}); input.dispatchEvent(new Event('input',{bubbles:true})); return true; })()`,returnByValue:true })
          if(!result.result.value) throw new Error('无法编辑草稿数量')
          await sleep(150); await clickByText(client,'保存草稿'); await sleep(500)
        }
        await setQuantity(2)
        let saved=await checkedJson(`/order-drafts/${draft.id}`,auth.token)
        if(saved.items[0].quantity!==2 || Number(saved.items[0].source_snapshot.original_quantity)!==10000) throw new Error('修改草稿覆盖了原数量或未保存')
        await clickByText(client,'生成草稿需求单'); await sleep(600)
        const docs=await checkedJson(`/biz-docs?order_draft_id=${draft.id}`,auth.token)
        if(docs.length!==1 || !docs[0].title.includes('草稿')) throw new Error('草稿文件未生成或未标注草稿')
        await checkedJson(`/quote-versions/${item.orderDraft.version_id}/submit-approval`,auth.token,{method:'POST',body:JSON.stringify({reason:'虚构订单草稿验收'})})
        await checkedJson(`/quote-versions/${item.orderDraft.version_id}/mark-sent`,auth.token,{method:'POST',body:JSON.stringify({})})
        await checkedJson(`/quote-versions/${item.orderDraft.version_id}/accept`,auth.token,{method:'POST'})
        await client.send('Page.navigate',{url:`${APP_BASE}/order-drafts/${draft.id}`}); await waitForText(client,'确认正式下单'); await sleep(400)
        await client.send('Runtime.evaluate',{expression:`document.querySelector('.page-container .semi-select')?.click()`})
        await sleep(300)
        if(await clickByText(client,item.orderDraft.quote_no,{tag:'[role="option"]'})!=='clicked') {
          const selected=await client.send('Runtime.evaluate',{expression:`(() => { const option=Array.from(document.querySelectorAll('.semi-select-option')).find(n=>n.innerText.includes(${JSON.stringify(item.orderDraft.quote_no)})); if(!option) return false; option.click(); return true; })()`,returnByValue:true})
          if(!selected.result.value) throw new Error('无法选择客户确认报价')
        }
        await sleep(200); await clickByText(client,'确认正式下单')
        if(!await waitForText(client,'草稿明细、币种或付款条件与客户确认报价不一致')) { const diag=await client.send('Runtime.evaluate',{expression: `JSON.stringify({text:document.body.innerText,buttons:Array.from(document.querySelectorAll('button')).filter(n=>n.innerText.includes('确认正式下单')).map(n=>({text:n.innerText,disabled:n.disabled,aria:n.getAttribute('aria-disabled')}))})`,returnByValue:true}); throw new Error('未观察到数量不符阻断：'+diag.result.value) }
        await setQuantity(10000)
        await clickByText(client,'确认正式下单')
        if(!await waitForText(client,'订单详情')) throw new Error('核对一致后没有进入正式订单')
        const orders=await checkedJson(`/orders?opportunity_id=${item.orderDraft.opportunity_id}`,auth.token)
        if(orders.total!==1 || orders.items[0].total_amount!==200000) throw new Error('没有按确认报价建立一张正式订单')
        if(!await waitForText(client,'CHKUI订单准备')) throw new Error('定制正式订单没有显示需求名称')
        const formalShot=await client.send('Page.captureScreenshot',{format:'png'})
        writeFileSync(join(OUT_DIR,'51-order-draft-confirmed.png'),Buffer.from(formalShot.data,'base64'))
        if (process.env.SMOKE_DELIVERY_PLANNING === '1') {
          const orderId = orders.items[0].id
          await clickByText(client, '跟单节点', {tag: '[role="tab"]'}); await sleep(500)
          await clickByText(client, '交期与计划')
          if (!await waitForText(client, '交期与跟单计划')) throw new Error('交期计划弹窗未打开')
          const setInput = async (selector, value) => {
            const res=await client.send('Runtime.evaluate',{expression:`(() => { const input=document.querySelector(${JSON.stringify(selector)}); if(!input)return false; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(String(value))}); input.dispatchEvent(new Event('input',{bubbles:true})); return true; })()`,returnByValue:true})
            if(!res.result.value) throw new Error('无法填写交期计划: '+selector)
            await sleep(150)
          }
          await setInput('.semi-modal-content input', '2026-10-30')
          await client.send('Runtime.evaluate',{expression:`document.querySelector('.semi-modal-content .semi-select')?.click()`})
          await sleep(150)
          await client.send('Runtime.evaluate',{expression:`Array.from(document.querySelectorAll('.semi-select-option')).find(n=>n.innerText.trim()==='到货日')?.click()`})
          await sleep(150)
          await setInput('.semi-modal-content .semi-input-number input', 3)
          await clickByText(client, '预览受影响面')
          if (!await waitForText(client, '建议发货日：2026-10-27')) throw new Error('到货日没有按运输天数倒排')
          let untouched=await checkedJson(`/orders/${orderId}/milestones`,auth.token)
          if(untouched.some(n=>n.planned_date)) throw new Error('预览错误写入计划')
          const previewShot=await client.send('Page.captureScreenshot',{format:'png'})
          writeFileSync(join(OUT_DIR,'52-delivery-planning-preview.png'),Buffer.from(previewShot.data,'base64'))
          await clickByText(client, '提交待确认计划'); await sleep(500)
          if((await checkedJson(`/orders/${orderId}`,auth.token)).delivery_kind) throw new Error('未确认就改了交期类型')
          await clickByText(client, '确认并重排')
          if(!await waitForText(client, '计划发货日：2026-10-27')) throw new Error('确认后订单日期没有刷新')
          const rows=await checkedJson(`/orders/${orderId}/milestones`,auth.token)
          const contract=rows.find(n=>n.node==='contract')
          await client.send('Runtime.evaluate',{expression:`(() => { const row=Array.from(document.querySelectorAll('tr')).find(n=>n.innerText.includes('签订合同')); Array.from(row?.querySelectorAll('a')||[]).find(n=>n.innerText==='登记')?.click(); })()`})
          if(!await waitForText(client,'登记里程碑：签订合同')) throw new Error('节点登记未打开')
          await client.send('Runtime.evaluate',{expression:`document.querySelector('.semi-modal-content .semi-select')?.click()`})
          await sleep(150)
          await client.send('Runtime.evaluate',{expression:`Array.from(document.querySelectorAll('.semi-select-option')).find(n=>n.innerText.includes('不适用，跳过此节点'))?.click()`})
          await sleep(150)
          await setInput('.semi-modal-content input[aria-label="跳过原因"]', '现货使用已签框架协议')
          await clickByText(client,'保存'); await sleep(500)
          const updated=(await checkedJson(`/orders/${orderId}/milestones`,auth.token)).find(n=>n.id===contract.id)
          if(updated.status!=='skipped' || updated.actual_date) throw new Error('跳过错误计作实际完成')
          const planShot=await client.send('Page.captureScreenshot',{format:'png'})
          writeFileSync(join(OUT_DIR,'53-delivery-planning-confirmed.png'),Buffer.from(planShot.data,'base64'))
        }

      }

      if (item.sampleSource) {
        if (!await waitForText(client, '按此版本申请打样')) throw new Error('草稿报价缺少打样入口')
        await clickByText(client, '按此版本申请打样')
        if (!await waitForText(client, '原采购数量：10,000')) throw new Error('原采购数量未展示')
        const defaultQty = await client.send('Runtime.evaluate', {
          expression: `document.querySelector('.semi-modal-content .semi-input-number input')?.value`, returnByValue: true,
        })
        if (Number(defaultQty.result.value) !== 1) throw new Error(`样品数量非默认 1：${defaultQty.result.value}`)
        const sourceShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '48-sample-source-quantities.png'), Buffer.from(sourceShot.data, 'base64'))
        await clickByText(client, '取消')
        await checkedJson(`/quotes/${item.sampleSource.quote_id}/versions`, auth.token, { method: 'POST' })
        await client.send('Page.navigate', { url: `${APP_BASE}/quotes/${item.sampleSource.quote_id}?version=${item.sampleSource.version_id}` })
        await waitForText(client, '按此版本申请打样')
        await clickByText(client, '按此版本申请打样')
        if (!await waitForText(client, '（历史版本）')) throw new Error('历史版本打样来源未标识')
        const changed = await client.send('Runtime.evaluate', {
          expression: `(() => {
            const dialog = document.querySelector('.semi-modal-content');
            const input = dialog?.querySelector('.semi-input-number input');
            const spec = dialog?.querySelector('textarea');
            if (!input || !spec) return false;
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '2');
            input.dispatchEvent(new Event('input', { bubbles: true }));
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(spec, '本次规格');
            spec.dispatchEvent(new Event('input', { bubbles: true })); return true;
          })()`, returnByValue: true,
        })
        if (!changed.result.value) throw new Error('无法修改本次数量和规格')
        await sleep(200)
        await clickByText(client, '建立打样申请')
        if (!await waitForText(client, '打样来源')) throw new Error('未进入打样详情')
        const samples = await checkedJson(`/samples?opportunity_id=${item.sampleSource.opportunityId}`, auth.token)
        const sample = samples.items.find(row => row.source_context?.id === item.sampleSource.version_id)
        if (!sample || sample.items[0].quantity !== 2 || sample.items[0].original_quantity !== 10000 || sample.items[0].specification !== '本次规格') throw new Error('数量或规格未分开保存')
        if (!await waitForText(client, '原规格：原规格')) throw new Error('详情未展示原规格差异')
        const detailShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '49-sample-source-detail.png'), Buffer.from(detailShot.data, 'base64'))
        await client.send('Page.navigate', { url: `${APP_BASE}/inquiries?opportunity_id=${item.sampleSource.opportunityId}` })
        if (!await waitForText(client, 'CHKUI来源采购')) throw new Error('询价来源未加载')
        if (await clickByText(client, '申请打样', { tag: 'a' }) !== 'clicked') throw new Error('询价没有打样入口')
        if (!await waitForText(client, '原采购数量：10,000')) throw new Error('询价未带入原数量')
        await clickByText(client, '取消')
        await checkedJson(`/custom-inquiries/${item.sampleSource.inquiryId}/revise`, auth.token, {
          method: 'POST', body: JSON.stringify({ quantity: 20000, revision_note: 'UI 历史版本验收' }),
        })
        await client.send('Page.navigate', { url: `${APP_BASE}/inquiries?opportunity_id=${item.sampleSource.opportunityId}` })
        await waitForText(client, '历史')
        await clickByText(client, '历史', { tag: 'a' })
        if (!await waitForText(client, '版本历史：')) throw new Error('询价历史未打开')
        let choseV1 = false
        for (let attempt = 0; attempt < 40; attempt += 1) {
          const choice = await client.send('Runtime.evaluate', {
            expression: `(() => {
              const row = Array.from(document.querySelectorAll('.semi-modal-content tr')).find(node => node.cells?.[0]?.innerText.trim() === 'v1');
              const link = row?.querySelector('a'); if (!link) return false; link.click(); return true;
            })()`, returnByValue: true,
          })
          if (choice.result.value) { choseV1 = true; break }
          await sleep(250)
        }
        if (!choseV1) throw new Error('无法在历史表选择 V1')
        if (!await waitForText(client, '（历史版本）') || !await waitForText(client, '原采购数量：10,000')) throw new Error('询价历史版本未按原数量取用')
        await clickByText(client, '取消')
        await client.send('Page.navigate', { url: `${APP_BASE}/samples/${sample.id}` })
        await waitForText(client, '打样来源')
      }

      if (item.demandFlow) {
        const oid = item.demandFlow.opportunityId
        if (!await waitForText(client, '关联单据')) throw new Error('商机未显示关联单据入口')
        if (await clickByText(client, '关联单据', { tag: '[role="tab"]' }) !== 'clicked') throw new Error('无法切换关联单据')
        if (!await waitForText(client, '暂无关联定制询价')) throw new Error('单据页未加载')
        await clickByText(client, '记录定制询价')
        if (!await waitForText(client, '记录本次需求的定制询价')) throw new Error('需求询价弹窗未打开')
        const inquiryTitle = 'CHKUI本次定制采购'
        const filled = await client.send('Runtime.evaluate', {
          expression: `(() => {
            const dialog = document.querySelector('.semi-modal-content');
            const input = dialog?.querySelector('input');
            if (!input) return false;
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(inquiryTitle)});
            input.dispatchEvent(new Event('input', { bubbles: true })); return true;
          })()`, returnByValue: true,
        })
        if (!filled.result.value) throw new Error('无法填写商机询价标题')
        await clickByText(client, '保存')
        if (!await waitForText(client, '定制询价已记录并关联当前商机')) throw new Error('商机询价保存失败')
        const inquiries = await checkedJson(`/custom-inquiries?opportunity_id=${oid}`, auth.token)
        const inquiry = inquiries.items.find((row) => row.title === inquiryTitle)
        if (!inquiry || inquiry.customer_id !== item.demandFlow.customerId) throw new Error('询价没有自动带入商机和客户')
        const before = await checkedJson(`/opportunities/${oid}`, auth.token)
        const quote = await checkedJson(`/custom-inquiries/${inquiry.id}/create-quote`, auth.token, {
          method: 'POST', body: JSON.stringify({ unit_cost: 10, quoted_price: 20 }),
        })
        const afterDraft = await checkedJson(`/opportunities/${oid}`, auth.token)
        if (before.stage_id !== afterDraft.stage_id) throw new Error('生成报价草稿错误推进了阶段')
        const approved = await checkedJson(`/quote-versions/${quote.version_id}/submit-approval`, auth.token, {
          method: 'POST', body: JSON.stringify({ reason: '虚构 UI 验收' }),
        })
        if (approved.version.approval_status !== 'approved') throw new Error('报价未自动通过审批，无法验证正式发送入口')
        await client.send('Page.navigate', { url: `${APP_BASE}/quotes/${quote.quote_id}` })
        if (!await waitForText(client, '标记已发送')) throw new Error('报价发送入口未就绪')
        await clickByText(client, '标记已发送')
        if (!await waitForText(client, '确认已实际发送')) throw new Error('报价发送确认未打开')
        await clickByText(client, '确认已实际发送')
        if (!await waitForText(client, '已标记为已发送')) throw new Error('报价发送失败')
        const sent = await checkedJson(`/opportunities/${oid}`, auth.token)
        if (sent.stage_code !== 'quoted') throw new Error('正式发送后商机未自动推进已报价')
        await client.send('Page.navigate', { url: `${APP_BASE}/opportunities/${oid}` })
        await waitForText(client, '关联单据')
        await clickByText(client, '关联单据', { tag: '[role="tab"]' })
        if (!await waitForText(client, inquiryTitle) || !await waitForText(client, quote.quote_no)) throw new Error('商机关联单据未显示询价和报价')
        const recordsShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '45-demand-quote-sent.png'), Buffer.from(recordsShot.data, 'base64'))
        await clickByText(client, '申请打样')
        if (!await waitForText(client, '新建样品申请')) throw new Error('打样申请未打开')
        const selected = await client.send('Runtime.evaluate', {
          expression: `document.querySelector('.semi-modal-content')?.innerText.includes('CHKUI独立采购需求')`, returnByValue: true,
        })
        if (!selected.result.value) throw new Error('打样申请未自动带入当前商机')
        const sampleShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '46-demand-sample-prefill.png'), Buffer.from(sampleShot.data, 'base64'))
        await clickByText(client, '取消')
        await client.send('Page.navigate', { url: `${APP_BASE}/inquiries?opportunity_id=${oid}` })
        if (!await waitForText(client, inquiryTitle) || !await waitForText(client, '当前商机：')) throw new Error('询价库未按商机筛选')
        await clickByText(client, '记录定制询价')
        if (!await waitForText(client, '关联商机（本次采购需求）')) throw new Error('询价库缺少商机关联字段')
        await sleep(300)
        const inquiryShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '47-demand-inquiry-link.png'), Buffer.from(inquiryShot.data, 'base64'))
        await clickByText(client, '取消')
        await client.send('Page.navigate', { url: `${APP_BASE}/opportunities/${oid}` })
        await waitForText(client, '关联单据')
        await clickByText(client, '关联单据', { tag: '[role="tab"]' })
      }

      if (item.workbenchAnalysis) {
        const before = await checkedJson('/agent/sessions', auth.token)
        const paused = []
        let intercept = true
        client.on((message) => {
          if (message.method !== 'Fetch.requestPaused') return
          if (intercept && message.params.request.method === 'POST') paused.push(message.params)
          else void client.send('Fetch.continueRequest', { requestId: message.params.requestId })
        })
        await client.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/v1/agent/sessions', requestStage: 'Request' }] })
        await waitForText(client, '分析本月成交冲刺机会', 5000)
        await client.send('Runtime.evaluate', {
          expression: `(() => { const button = [...document.querySelectorAll('button')].find((el) => el.innerText.includes('分析本月成交冲刺机会')); button.click(); button.click(); })()`,
        })
        if (!await waitForText(client, 'Copilot 正在查数据', 5000)
            || !await waitForText(client, '本次仅提供分析，不新增或修改业务记录', 2000)) {
          problems.push('工作台分析：没有自动带入提问或创建会话阶段没有等待状态')
        }
        if (paused.length !== 1) problems.push(`工作台分析：连续点击创建了 ${paused.length} 个会话请求`)
        const pendingShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '43-workbench-analysis-pending.png'), Buffer.from(pendingShot.data, 'base64'))
        for (const request of paused) {
          await client.send('Fetch.fulfillRequest', {
            requestId: request.requestId, responseCode: 503,
            responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
            body: Buffer.from(JSON.stringify({ code: 50300, message: '本地模拟创建会话失败', data: null })).toString('base64'),
          })
        }
        intercept = false
        await client.send('Fetch.disable')
        if (!await waitForText(client, '本地模拟创建会话失败', 5000)) problems.push('工作台分析：创建失败没有可读提示')
        const restored = await client.send('Runtime.evaluate', {
          expression: "document.querySelector('aside input')?.value ?? ''", returnByValue: true,
        })
        if (!restored.result.value.includes('分析本月成交冲刺机会')) problems.push('工作台分析：失败后问题丢失，不能重试')
        await clickByText(client, '发送')
        // 安全前置强制 AI key 为空；验收真实提问已提交，并显示模型未配置的真实结果。
        if (!await waitForText(client, 'Agent 还没有配置模型', 8000)) problems.push('工作台分析：重试后未显示服务端结果')
        const after = await checkedJson('/agent/sessions', auth.token)
        const created = after.filter((row) => !before.some((previous) => previous.id === row.id))
        if (created.length !== 1) problems.push('工作台分析：重试没有创建唯一会话')
        if (created.length === 1) {
          const detail = await checkedJson(`/agent/sessions/${created[0].id}`, auth.token)
          const users = detail.messages.filter((message) => message.role === 'user')
          if (users.length !== 1 || !users[0].content.includes('分析本月成交冲刺机会')
              || !users[0].content.includes('仅提供分析') || detail.actions.length !== 0) {
            problems.push('工作台分析：没有唯一自动提问，或产生了非分析写动作')
          }
        }
        const location = await client.send('Runtime.evaluate', { expression: 'window.location.pathname', returnByValue: true })
        if (location.result.value !== '/workbench') problems.push('工作台分析仍只跳转 AI 页面')
      }

      if (item.quoteLifecycle) {
        const { quoteId: qid, versionId: vid } = item.quoteLifecycle
        const actionButtons = async () => (await client.send('Runtime.evaluate', {
          expression: "[...document.querySelectorAll('.page-container button')].map((el) => el.innerText.trim())", returnByValue: true,
        })).result.value
        await waitForText(client, '标记已发送', 5000)
        const before = await actionButtons()
        if (before.some((t) => ['客户接受', '客户拒绝', '转销售订单'].includes(t))) {
          problems.push('未发送报价仍显示客户结果或转单按钮')
        }
        await clickByText(client, '标记已发送')
        if (!await waitForText(client, '确认已实际发送', 5000)) problems.push('发送确认没有说明实际发送事实')
        await clickByText(client, '确认已实际发送')
        if (!await waitForText(client, '客户接受', 5000)) problems.push('正式发送后没有客户结果操作')
        await clickByText(client, '客户拒绝')
        if (!await waitForText(client, '记录客户拒绝', 5000)) problems.push('客户拒绝没有打开原因弹窗')
        const reason = 'CHKUI客户要求的交期无法满足'
        const filled = await client.send('Runtime.evaluate', {
          expression: `(() => {
            const input = [...document.querySelectorAll('textarea')].find((el) => el.placeholder.includes('实际拒绝原因'));
            if (!input || input.value !== '') return false;
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, ${JSON.stringify(reason)});
            input.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
          })()`, returnByValue: true,
        })
        if (!filled.result.value) problems.push('拒绝原因缺少输入或预填了虚构原因')
        const dialogShot = await client.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT_DIR, '41-quote-decline-reason.png'), Buffer.from(dialogShot.data, 'base64'))
        await clickByText(client, '确认记录')
        if (!await waitForText(client, '已记录客户拒绝', 5000)) problems.push('客户拒绝保存失败')
        const detail = await checkedJson(`/quote-versions/${vid}`, auth.token)
        const logs = await checkedJson(`/quote-versions/${vid}/send-logs`, auth.token)
        const follows = await checkedJson(`/followups?quote_id=${qid}&page_size=100`, auth.token)
        if (detail.quote.status !== 'declined' || !detail.version.declined_at || logs.length !== 1
            || !follows.items.some((row) => row.quote_id === qid && row.content.includes(reason))) {
          problems.push('页面输入的拒绝原因未按正式事实保存')
        }
        const terminalButtons = await actionButtons()
        if (terminalButtons.some((t) => ['客户接受', '客户拒绝', '标记已发送', '登记再次发送', '转销售订单'].includes(t))) {
          problems.push('客户拒绝后仍显示无效的报价动作')
        }
      }

      if (item.followupPlan || item.followupExemption || item.followupSchedule) {
        const typed = await client.send('Runtime.evaluate', {
          expression: `(() => {
            const input = document.querySelector('textarea[placeholder="今天沟通了什么？"]');
            if (!input) return false;
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '${item.followupSchedule ? 'CHKUI计划跟进' : 'CHKUI免填跟进'}');
            input.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
          })()`, returnByValue: true,
        })
        if (!typed.result.value) problems.push('跟进表单：无法填写沟通结论')
        await sleep(200)
        if (item.followupPlan) {
          await clickByText(client, '保存')
          if (!await waitForText(client, '请填写下一动作和下次跟进时间，或选择免填原因', 3000)) {
            problems.push('跟进表单：缺计划没有阻止保存')
          }
        } else if (item.followupSchedule) {
          await client.send('Runtime.evaluate', {
            expression: `(() => {
              const set = (input, value) => {
                input.focus();
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
                input.dispatchEvent(new Event('input', { bubbles: true }));
              };
              set(document.querySelector('input[placeholder="例如：整理报价并回访客户"]'), 'CHKUI回访客户');
              set(document.querySelector('input[placeholder="选择下次跟进时间"]'), '2026-10-12 09:00:00');
            })()`, returnByValue: true,
          })
          await sleep(300)
          await client.send('Runtime.evaluate', { expression: 'document.activeElement.blur()', returnByValue: true })
          await sleep(300)
          await clickByText(client, '保存')
          if (!await waitForText(client, '跟进已记录，并生成了后续任务', 5000)) problems.push('跟进表单：计划保存失败')
          const response = await fetch(`${API_BASE}/api/v1/followups?customer_id=${customerId}&page_size=200`, { headers: { Authorization: `Bearer ${auth.token}` } })
          const rows = (await response.json()).data
          const saved = rows.items.filter((row) => row.content === 'CHKUI计划跟进')
          if (saved.length !== 1 || saved[0].next_action !== 'CHKUI回访客户' || !saved[0].next_task_id || !saved[0].task_due_at) {
            problems.push('跟进表单：计划/关联任务没有正确保存')
          }
        } else {
          const selection = await client.send('Runtime.evaluate', {
            expression: `(() => {
              const control = [...document.querySelectorAll('.semi-select')].find((el) => el.innerText.includes('安排下一次跟进'));
              if (!control) return 'missing-select';
              control.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
              control.click();
              return 'clicked';
            })()`, returnByValue: true,
          })
          if (selection.result.value !== 'clicked') problems.push('跟进表单：没有打开后续安排下拉')
          await sleep(200)
          const choice = await client.send('Runtime.evaluate', {
            expression: `(() => {
              const option = [...document.querySelectorAll('[role="option"], .semi-select-option')].find((el) => el.innerText.includes('免填：等待外部固定节点'));
              if (!option) return 'missing-option';
              option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
              option.click();
              return 'clicked';
            })()`, returnByValue: true,
          })
          if (choice.result.value !== 'clicked') problems.push('跟进表单：没有选中免填原因')
          if (!await waitForText(client, '保存免填原因，本次不创建后续待办。', 3000)) {
            problems.push('跟进表单：免填说明没有显示')
          }
          await clickByText(client, '保存')
          if (!await waitForText(client, '跟进已记录', 5000)) problems.push('跟进表单：免填保存失败')
          const result = await fetch(`${API_BASE}/api/v1/followups?customer_id=${customerId}&page_size=200`, { headers: { Authorization: `Bearer ${auth.token}` } })
          const rows = (await result.json()).data
          const saved = rows.items.filter((row) => row.content === 'CHKUI免填跟进')
          if (saved.length !== 1 || saved[0].exemption_reason !== 'waiting_external' || saved[0].next_task_id != null) {
            problems.push('跟进表单：免填记录/任务不符合要求')
          }
        }
      }

      if (item.ruleSave) {
        for (const [heading, endpoint, toast] of [
          ['自动任务规则', '/task-rules', '自动任务规则已保存'],
          ['公海回收规则', '/public-pool/rules', '公海规则已保存'],
        ]) {
          const beforeResponse = await fetch(`${API_BASE}/api/v1${endpoint}`, { headers: { Authorization: `Bearer ${auth.token}` } })
          const before = (await beforeResponse.json()).data[0]
          const changed = await client.send('Runtime.evaluate', {
            expression: `(() => {
              const heading = [...document.querySelectorAll('div')].find((el) => el.childElementCount === 0 && el.innerText === ${JSON.stringify(heading)});
              const input = heading?.parentElement.querySelector('input');
              if (!input) return null;
              const next = Number(input.value) + 1;
              input.focus();
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, String(next));
              input.dispatchEvent(new Event('input', { bubbles: true }));
              input.blur();
              return next;
            })()`, returnByValue: true,
          })
          if (!changed.result.value || !await waitForText(client, toast, 5000)) {
            problems.push(`${heading}：修改天数未保存`)
            continue
          }
          const afterResponse = await fetch(`${API_BASE}/api/v1${endpoint}`, { headers: { Authorization: `Bearer ${auth.token}` } })
          const after = (await afterResponse.json()).data.find((row) => row.id === before.id)
          const days = endpoint === '/task-rules' ? after.trigger_config.days : after.days
          if (days !== changed.result.value || after.code !== before.code || after.name !== before.name || after.level !== before.level) {
            problems.push(`${heading}：部分更新结果错误或覆盖原有字段`)
          }
        }
      }
      const missing = []
      for (const text of item.expect) {
        // 多个候选文案满足其一即可（看板可能因为没数据只显示空态）
        const hit = await waitForText(client, text, 3000)
        missing.push(hit ? null : text)
      }
      const missingReal = missing.filter(Boolean)
      // expect 里任意一条命中就算通过，避免把"没数据"误判成功能缺失
      if (item.expectAll ? missingReal.length > 0 : missingReal.length === item.expect.length) {
        problems.push(`${item.name}：页面上找不到 ${item.expect.join(' / ')}`)
      }

      await sleep(400)
      const shot = await client.send('Page.captureScreenshot', { format: 'png' })
      const file = join(OUT_DIR, `${item.name}.png`)
      writeFileSync(file, Buffer.from(shot.data, 'base64'))
      console.log(`${ok ? '✓' : '✗'} [交互] ${item.path} + ${item.clicks.join('+') || '直接看'} → ${file}`)
      if (item.sourcePath) {
        const clicked = await client.send('Runtime.evaluate', {
          expression: `(() => { const link = document.querySelector('a[href="${item.sourcePath}"]'); if (!link) return false; link.click(); return true })()`,
          returnByValue: true,
        })
        const opened = clicked.result.value && await waitForText(client, item.sourceText ?? '订单明细', 5000)
        const location = await client.send('Runtime.evaluate', {
          expression: 'window.location.pathname', returnByValue: true,
        })
        if (!opened || location.result.value !== item.sourcePath) {
          problems.push('客户时间线：原单链接没有打开对应单据')
        }
      }

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
    // 清理临时 profile。这里失败**不能算测试失败**：
    // 上一次运行被强杀时浏览器进程可能还占着目录（Windows 上会 EPERM），
    // 之前就因此报过一次假的"冒烟测试失败"，把真正的结论盖掉了。
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        rmSync(PROFILE_DIR, { recursive: true, force: true })
        break
      } catch (error) {
        if (attempt === 2) {
          console.log(
            `（提示：临时目录 ${PROFILE_DIR} 没清掉，不影响本次测试结论：${error.code ?? error.message}）`,
          )
        } else {
          await sleep(500)
        }
      }
    }
    // 收尾清扫放在最后：不管冒烟成功还是中途失败，都要把本轮写出来的通知清掉
    cleanTestResidue(runStartedAt)
  }
}

main().catch((error) => {
  console.error(`✗ 冒烟测试失败：${error.message}`)
  process.exitCode = 1
})
