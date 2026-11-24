# F14 前端框架 / 登录 / 权限守卫复核：13 条成立（含 1 条潜伏）

- 范围：`frontend/src/app` 路由/菜单/权限守卫、`modules/auth/LoginPage`、`modules/agent/AgentPage`、`shared/api`（client/agent/auth/dingtalk）、`shared/store`（auth/tabs/copilot）、`shared/beijing-time`、`safe-url` 等。
- 方式：逐条重读当前代码；`review/repro/f14_frontend/check_f14.mjs` 做静态断言，并实测 `dateStyle` 的运行时行为；没有改任何业务代码。
- 命令：`node review/repro/f14_frontend/check_f14.mjs`
- 结果：**原报告 12 条全部成立；新增 1 条漏项 F14-13；F14-04 建议由 P2 降 P3；F14-09 确认为潜伏（当前无调用点）。0 条误报。**
- 原报告头部写“待确认 0”，但逐文件表里提到 `F14-T01` 却没给定义。复核后端 `create_inquiry_instance` 已按 `(inquiry_id, inquiry_version)` 复用同一行、同一请求号，前端发起 OA 审批不需要再带键；`F14-T01` 不构成缺陷，不计入。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F14-01 | P2 | 确认 | AI 会话详情失败显示“这段对话还没有消息”，待确认动作一起消失。 |
| F14-02 | P2 | 确认 | 确认 AI 建报价/版本后只失效四个域，报价列表/详情缓存不失效。 |
| F14-03 | P2 | 确认 | AI 流式发送和建会话都没有请求键，断流重试会重复消息/会话。 |
| F14-04 | P3（原 P2） | 确认降级 | API 客户端不统一 humanize，技术开关名/上游原文可进 Toast；但后端多数错误已是中文，影响文案不影响流程。 |
| F14-05 | P2 | 确认 | `/pricing`、`/order-drafts`、`/pool-recycle` 无菜单项，权限守卫对 `!menu` 直接放行。 |
| F14-06 | P3 | 确认 | 已有会话发送失败只 Toast，不把清空的输入恢复。 |
| F14-07 | P3 | 确认（潜伏） | 钉钉 `authorize_url` 未走 `isSafeExternalUrl`，后端配置被污染可跳 `javascript:`。 |
| F14-08 | P3 | 确认 | 登录成功但 `fetchMe` 失败被吞，token 有、user 空，首屏被守卫判“没有权限”。 |
| F14-09 | P3 | 确认（潜伏） | `dateStyle/timeStyle` 被归到时间部分，`toLocaleTimeString` 必抛 TypeError；当前无调用点。 |
| F14-10 | P3 | 确认 | 多页签淘汰只提示最后一个，关模块连带删详情时提示更少。 |
| F14-11 | P3 | 确认 | 所有待确认卡共用一个 `confirmMutation.isPending`，按钮一起转；取消按钮无 disabled。 |
| F14-12 | P3 | 确认 | AI 会话删除入口是纯 `<span>`，键盘不可达。 |
| **F14-13** | **P2** | **新增确认** | 原报告只盯报价缓存；实际商机下一步、审批提交、跟进时间线等动作也都不失效对应缓存。 |

## 二、P2 复核

### F14-01 会话详情失败当空会话

`AgentPage.tsx:77-81、501-516`：

```tsx
const detailQuery = useQuery({
  queryKey: ['agent-session', sessionId],
  queryFn: () => getAgentSession(sessionId!),
  enabled: Boolean(sessionId),
})
...
{messages.length === 0 && (!sessionId || !detailQuery.isPending) && (
  <div className="agent-empty">
    <h2>{sessionId ? '这段对话还没有消息' : '开始新对话'}</h2>
```

没有 `detailQuery.isError`。500/403/超时后 `isPending=false`、`messages=[]`、`actions=[]`，落“这段对话还没有消息”，连原来待确认的 L2/L3 动作也看不到。用户可能重新发问，或以为动作已经丢了。

确认 P2。改法：详情失败单独显示“会话读取失败，重试”，并区分 403/404/500；错误态不要渲染空会话和快捷发送。

### F14-02 报价缓存不失效

`AgentPage.tsx:233-243` 的 `confirmMutation.onSuccess` 只失效：

```tsx
void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
void queryClient.invalidateQueries({ queryKey: ['customers'] })
void queryClient.invalidateQueries({ queryKey: ['tasks'] })
void queryClient.invalidateQueries({ queryKey: ['followups'] })
```

`create_quote_draft` / `create_quote_version` 成功后，报价列表 key 是 `['quotes', {...}]`，版本是 `['quote-versions', quoteId]`、`['quote-version', versionId]`，都不在失效范围。React Query 默认 `staleTime=0`，但已经挂载的报价页签不会因为切换页签自动重取，用户会看到确认前的列表/版本。

确认 P2。改法：按 `action.tool_name` 精确失效；至少补 `quotes`、`quote-versions`、`quote-version`，以及动作影响到的商机/审批域（见 F14-13）。

### F14-03 流式发送/建会话无幂等键

`shared/api/agent.ts:124-140`：

```ts
body: JSON.stringify({ content }),
```

`AgentPage.tsx:206-230` 建会话：

```tsx
createAgentSession({
  title: content.slice(0, 20),
  context_type: contextType ?? undefined,
  context_id: contextId ?? undefined,
})
```

都不带 `request_key`/客户端消息 id。断流、刷新、网关超时后重发同一条问题，服务端会再写一条用户消息、再跑一轮只读工具；新建会话分支还会在服务端留下一个空会话，因为第一次请求其实已经成功、只是响应丢了。

确认 P2。改法：发送时生成请求键，失败重试复用同一键，后端按 `(session_id, request_key)` 幂等；建会话也带键或让后端按客户端会话 id 复用。

### F14-05 深链绕过权限守卫

`router.tsx:107-108、124、129` 挂了三个页面：

```tsx
{ path: 'order-drafts', element: <OrderDraftsPage /> },
{ path: 'pool-recycle', element: <PoolRecyclePage /> },
{ path: 'pricing', element: <PricingPage /> },
```

`menu.ts` 没有这三项（`/pricing` 明确只留深链，`/pool-recycle` 只给老链接兜底）。`RoutePermissionGuard.tsx:39`：

```tsx
if (!menu || menu.key === 'settings' || hasPermission(user, menu.permission)) {
  return <Outlet />
}
```

`matchMenu` 找不到就放行，无权限用户看到的是页面壳 + 接口 403/空数据，不是“你没有这个模块权限”。后端仍拦数据，不是越权，但会被当成系统坏了。

确认 P2。改法：补隐藏菜单项/`PATH_PERMISSION` 表；`!menu` 时按路径前缀判权限，而不是一律放行。

## 三、P3 复核

### F14-04 API 客户端不 humanize（原 P2，降 P3）

`client.ts:147-173`：

```ts
function readableMessage(raw: unknown, code: number): string {
  const text = typeof raw === 'string' ? raw.trim() : ''
  if (!text || text === 'null' || text === 'undefined') {
    return code ? `系统内部错误（错误码 ${code}）...` : '网络异常，请稍后重试'
  }
  return text
}
```

出口没调用 `humanizeBackendText`。但后端 `AppError` 的 message 大多是中文，500 也统一成“服务器内部错误”；真正会漏的是外部开关名（如 `DINGTALK_PUSH_OFF`）或上游原样错误。它影响的是文案，不会阻断操作，也不是每次错误都泄露。

确认现象，**建议 P3**，不建议按 P2 排期。改法：在 `readableMessage` 或 `ApiError` 出口统一 humanize；原始信息另放 `technicalMessage` 只进控制台。

### F14-06 发送失败丢输入

`AgentPage.tsx:206-230`：

```tsx
const content = text.trim()
...
setInput('')
...
void runStream(sessionId, content)
```

`runStream` 的 catch 只 Toast，没有 `setInput(content)`；只有新建会话的 `createAgentSession().catch` 会恢复。已有会话失败后用户刚写的问题没了。

确认 P3。改法：没收到任何 delta 时恢复输入；已经有部分回复则保留“重新生成”，不要覆盖用户后续输入。

### F14-07 钉钉授权地址未校验协议

`LoginPage.tsx:76-89`：

```tsx
const { authorize_url } = await dingtalkAuthorize()
...
window.location.href = authorize_url
```

`shared/safe-url.ts` 已有 `isSafeExternalUrl`，登录页没用。后端配置/上游被污染成 `javascript:` 时，点击扫码会执行该 URL。来源是后端、触发条件不低，属潜伏安全缺口。

确认 P3（潜伏）。改法：跳转前 `isSafeExternalUrl` 白名单，后端也只允许 https 钉钉授权域名。

### F14-08 登录后 `fetchMe` 失败

`LoginPage.tsx:54-68`：

```tsx
setAuth(result.access_token, null)
const epoch = currentAuthEpoch()
try {
  const me = await fetchMe()
  if (currentAuthEpoch() === epoch) setUser(me)
} catch {
  // 用户信息拉取失败不阻塞登录
}
navigate(returnTo, { replace: true })
```

token 已写入、user 为 null，随后进业务页。`RoutePermissionGuard` 对 null user 判无权限，除工作台/设置外都显示“没有权限”。`useCurrentUserSync` 60 秒后会重试，但首屏是错误结论。

确认 P3。改法：`fetchMe` 失败显示“登录成功但权限加载失败，重试/退出”，或 AppLayout 在 `token && !user` 时全屏加载/重试，不要按无权限渲染。

### F14-09 `dateStyle/timeStyle` 分错组（潜伏）

`beijing-time.ts:28-64`：

```ts
const DATE_PART_KEYS = ['year', 'month', 'day', 'weekday', 'era'] as const
...
if ((DATE_PART_KEYS as readonly string[]).includes(key)) {
  ;(date as Record<string, unknown>)[key] = value
} else {
  ;(time as Record<string, unknown>)[key] = value
}
```

`dateStyle`/`timeStyle` 都落到 `time`。实测：

```text
new Date(...).toLocaleTimeString('zh-CN', { dateStyle: 'long' })
→ TypeError: Invalid option : dateStyle
```

确认会抛。但全仓没有调用点传这两个复合选项，当前不会触发，属潜伏。改法：`dateStyle` 归日期、`timeStyle` 归时间，或入口拒绝复合选项并补单测。

### F14-10 多页签淘汰只提示最后一个

`tabs.ts:65-100、106-119`：

```ts
let closed: TabItem | null = null
for (const victim of detailVictims) {
  if (result.length <= TAB_LIMIT) break
  result = result.filter((item) => item.key !== victim.key)
  closed = victim
}
...
result = result.filter((item) => item.key !== victim.key && item.parentKey !== victim.key)
closed = victim
```

`closed` 是单值，循环覆盖；关模块还会连带删它的详情子页签，但 `autoClosed` 仍只保留最后一个。用户只看到“已自动关闭 X”，不知道实际关了几个。

确认 P3。改法：返回 `closed: TabItem[]` 或计数，提示“已自动关闭 N 个（最近：X）”。

### F14-11 动作按钮共用 pending

`AgentPage.tsx:311-323`：

```tsx
<Button ... loading={confirmMutation.isPending} onClick={() => confirmMutation.mutate(action.id)}>
...
<Button size="small" onClick={() => rejectMutation.mutate(action.id)}>取消</Button>
```

所有待确认卡都读同一个 `confirmMutation.isPending`，点一条全部转圈；取消按钮没有 disabled，可以并发点另一条。后端状态机兜底，但界面状态误导。

确认 P3。改法：按 `action.id` 维护 pending/独立 mutation；至少 `disabled={confirmMutation.isPending || rejectMutation.isPending}`。

### F14-12 删除入口键盘不可达

`AgentPage.tsx:396-404`：

```tsx
<Popconfirm title="删除这个会话？" ...>
  <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }} onClick={(e) => e.stopPropagation()}>
    ✕
  </span>
</Popconfirm>
```

纯 `<span>`，无 `role/tabIndex/onKeyDown`，Tab 聚焦不到。确认 P3。改法：换成 `Button theme="borderless"` 或补 `role="button" tabIndex={0}` + Enter/Space + `aria-label`。

### F14-13 新增：动作缓存只失效四个域

原报告 F14-02 只写“报价缓存不失效”，但确认动作能执行的工具不止报价：

- `create_followup` → 应失效 `timeline`、`followups`、`notifications`；
- `create_task` → `tasks`（已覆盖）；
- `update_opportunity_next_action` → `opportunity`、`opportunities`、`funnel`、看板；
- `request_quote_approval` → `approvals`、`quote-version`、`quote-versions`、`notifications`；
- `create_quote_draft` / `create_quote_version` → `quotes`、`quote-versions`、商机报价计数。

当前 `onSuccess` 只有 `agent-session`、`customers`、`tasks`、`followups`。商机详情/列表、审批中心、时间线、通知角标都会继续显示旧值；用户以为 AI 没有执行，实际后端已经写入。

确认 P2（新增漏项）。改法：按 `action.tool_name` 建失效映射表，而不是固定四个 key；动作返回目标 id 时按 id 失效详情。

## 四、本块最大漏项

**F14-13**：原报告只看到“报价缓存没失效”，没把 `update_opportunity_next_action`、`request_quote_approval`、`create_followup` 一起看。AI 动作是一个入口执行多种写操作，缓存失效必须按 `tool_name` 分域；只补 `quotes` 仍然会让商机下一步、审批列表、时间线保持旧值。

若只算原报告已有条目，最大风险是 **F14-01**：会话详情失败时待确认动作一起消失，用户看不到本该确认/拒绝的 L2/L3 动作。

## 五、残余

- F14-04 的“原文进 Toast”取决于具体后端 message；本轮确认的是出口没有统一 humanize，不是每个错误都会泄露。
- F14-07、F14-09 都是潜伏：前者要后端配置被污染，后者要调用方传 `dateStyle/timeStyle`；当前主流程不触发。
- F14-T01 不记缺陷：后端已按业务键幂等，前端发起 OA 不需要 request key。
- 本轮只改 `review/`，未改业务代码；`check_f14.mjs` 结果 `14 confirmed/pending, 0 missing`（含 1 条运行时验证，13 条发现）。
