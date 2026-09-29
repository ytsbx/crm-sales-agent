#!/usr/bin/env node
/**
 * 界面冒烟测试：用无头浏览器打开 CRM，注入登录态后逐页截图，并收集控制台报错。
 *
 * 用法：
 *   node ops/smoke_ui.mjs
 *   SMOKE_USER=admin SMOKE_OUT=/tmp/shots node ops/smoke_ui.mjs
 *
 * 注意：报价中心为空时，脚本会**临时造一张报价**才能验证 What-if 面板，
 * 而报价没有删除接口（软删只对列表生效），所以跑完请在界面上手动删掉那张
 * 「Q开头」的测试报价，或者直接忽略它——它不影响任何统计口径。
 *
 * 为什么要有这个脚本：这个项目要分 6 个阶段做，每做完一段都得确认「页面真的能打开、
 * 数据真的能读出来」，而不是只看构建有没有过。它用浏览器调试协议驱动 Edge/Chrome，
 * 只依赖 Node 内置能力，不额外装 Playwright。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

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
    const created = await fetch(`${API_BASE}/api/v1/quotes`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ customer_id: customerId }),
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
 * 订单中心为空时自动造一张**真实订单**（建报价 → 提交审批 → 转订单），
 * 让 /orders/{id} 这条用例测到的是真页面而不是 404 兜底。
 * 任何一步没走通就返回 null（用例退回 /orders/1 兜底路径，不算失败）。
 */
async function createOrderIfEmpty(token, customerId) {
  if (!customerId) return null
  try {
    const headers = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    }
    const created = await fetch(`${API_BASE}/api/v1/quotes`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ customer_id: customerId }),
    }).then((r) => r.json())
    const versionId = created?.data?.version_id
    if (!versionId) return null
    const skus = await fetch(`${API_BASE}/api/v1/pricing/sku-options`, { headers }).then((r) =>
      r.json(),
    )
    const skuId = skus?.data?.[0]?.id
    if (skuId) {
      await fetch(`${API_BASE}/api/v1/quote-versions/${versionId}/items/batch`, {
        method: 'POST',
        headers,
        body: JSON.stringify([{ sku_id: skuId, quantity: 100, quoted_price: 28 }]),
      })
    }
    const submitted = await fetch(
      `${API_BASE}/api/v1/quote-versions/${versionId}/submit-approval`,
      { method: 'POST', headers, body: JSON.stringify({ reason: '冒烟造订单' }) },
    ).then((r) => r.json())
    // 走了人工审批（未自动通过）就没法自动转订单——放弃，退回兜底路径
    if (submitted?.data?.version?.approval_status !== 'approved') return null
    const converted = await fetch(
      `${API_BASE}/api/v1/quote-versions/${versionId}/convert-to-order`,
      { method: 'POST', headers, body: JSON.stringify({}) },
    ).then((r) => r.json())
    return converted?.data?.order_id ?? null
  } catch {
    return null
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
  rmSync(PROFILE_DIR, { recursive: true, force: true })
  mkdirSync(OUT_DIR, { recursive: true })

  const auth = await apiLogin()
  console.log(`✓ 接口登录成功：${auth.user.name}（${auth.user.roles.join(',')}）`)
  // 按真实数据组装用例
  const [customerId, opportunityId, productId, quoteId, skuId, firstOrderId] = await Promise.all([
    firstId('/customers', auth.token),
    firstId('/opportunities', auth.token),
    firstId('/products', auth.token),
    firstId('/quotes', auth.token),
    firstId('/pricing/sku-options', auth.token),
    firstId('/orders', auth.token),
  ])
  // 订单中心为空（全新库/刚清库）就造一张真实订单，别让详情页用例测 404 兜底
  let orderId = firstOrderId
  if (!orderId) {
    orderId = await createOrderIfEmpty(auth.token, customerId)
    console.log(
      orderId
        ? `✓ 订单中心是空的，已自动「建报价→审批→转订单」造一张订单 #${orderId}`
        : '（提示：自动造订单失败，订单详情用例退回 /orders/1 兜底）',
    )
  }
  const PAGES = [
    { path: '/workbench', name: '01-workbench' },
    { path: '/analytics', name: '02-analytics' },
    { path: '/leads', name: '03-leads' },
    { path: '/customers', name: '04-customers' },
    // 撞单裁定（验收20）：系统摆证据、归属由人裁定
    { path: '/duplicate-cases', name: '04b-duplicate-cases' },
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
    { path: '/settings?tab=tags', name: '23-settings-tags' },
    { path: '/settings?tab=notifications', name: '31-settings-notifications' },
    { path: '/settings?tab=roles', name: '24-settings-roles' },
    { path: '/settings?tab=departments', name: '25-settings-departments' },
    { path: '/agent', name: '20-agent' },
    { path: '/samples', name: '22-samples' },
    { path: '/wecom', name: '30-wecom' },
    {
      // 带参数进入，才能真正验证"选了 SKU 能出计费重与方案"，
      // 否则只截图到空状态，等于没验证结果区
      path: `/logistics?sku_id=${skuId ?? 1}&quantity=3000&destination=%E5%8D%8E%E4%B8%9C`,
      name: '21-logistics',
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
            'Boolean(document.querySelector("#root, #app")?.children.length) && document.body.innerText.trim().length > 0',
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
        expression: 'document.body.innerText.slice(0, 120).replace(/\\s+/g, " ")',
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
      console.log(`${rendered ? '✓' : '✗'} ${page.path} → ${file}`)
      console.log(`  页面首屏文本：${text.result.value}`)
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

      for (const label of item.clicks) {
        const outcome = await clickByText(client, label)
        if (outcome !== 'clicked') {
          const diag = await client.send('Runtime.evaluate', {
            expression:
              'JSON.stringify({ url: location.href, html: document.body.innerHTML.slice(0, 800) })',
            returnByValue: true,
          })
          problems.push(`点不到「${label}」（${item.path}） → ${diag.result.value}`)
        }
        await sleep(500)
      }

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
  }
}

main().catch((error) => {
  console.error(`✗ 冒烟测试失败：${error.message}`)
  process.exitCode = 1
})
