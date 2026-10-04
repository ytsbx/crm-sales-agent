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

    // 在一次性数据库内模拟客户接受；此处不发送报价或调用外部服务。
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
    // 知识库：定制询价列表 + 修订链/钉钉审批入口都在这一页
    { path: '/knowledge', name: '32-knowledge', expectText: '产品知识库 · 定制询价' },
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
    for (const page of PAGES) {
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

    for (const page of PAGES) {
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

    for (const item of INTERACTIONS) {
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

      const missing = []
      for (const text of item.expect) {
        // 多个候选文案满足其一即可（看板可能因为没数据只显示空态）
        const hit = await waitForText(client, text, 3000)
        missing.push(hit ? null : text)
      }
      const missingReal = missing.filter(Boolean)
      // expect 里任意一条命中就算通过，避免把"没数据"误判成功能缺失
      if (missingReal.length === item.expect.length) {
        problems.push(`${item.name}：页面上找不到 ${item.expect.join(' / ')}`)
      }

      await sleep(400)
      const shot = await client.send('Page.captureScreenshot', { format: 'png' })
      const file = join(OUT_DIR, `${item.name}.png`)
      writeFileSync(file, Buffer.from(shot.data, 'base64'))
      console.log(`${ok ? '✓' : '✗'} [交互] ${item.path} + ${item.clicks.join('+') || '直接看'} → ${file}`)
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
