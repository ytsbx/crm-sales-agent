# F13 审计标签 / 通用组件 / 布局复核：15 条确认 + 1 条证伪

- 范围：`AppLayout.tsx`、`GlobalSearch.tsx`、`DecisionMakerCard.tsx`、`CopilotDrawer.tsx`、`FollowUpModal.tsx`、`NotificationBell.tsx`、`PaymentVoucherControl.tsx`、`Timeline.tsx`、`audit-labels.ts`、`PermissionPicker.tsx`、`SettingsPage.tsx`（权限弹窗）、`AgentInsight.tsx`、`LazyRegion.tsx`、`MaskedContact.tsx`，以及相关后端通知/联系人口径。
- 方式：逐条重读当前代码；`review/repro/f13_frontend/check_f13.mjs` 做静态断言；没有改任何业务代码。
- 命令：`node review/repro/f13_frontend/check_f13.mjs`
- 结果：**原报告 14 条全部成立（F13-T01 已证伪为不可达）；新增 1 条漏项 F13-15。0 条整条误报。**
- 一处修正：F13-05 原报告把“数组值”也算进少报，实际数组会显示“N 项”，不会丢；少报发生在对象值和未知英文枚举值。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F13-01 | P1 | 确认 | 账号生成弹窗明文展示固定初始密码 `123456`。 |
| F13-02 | P2 | 确认 | 联系人查询失败显示“该客户还没有登记联系人”，并引导去加联系人。 |
| F13-03 | P2 | 确认 | Copilot 会话详情失败时 messages/actions 全空，待确认动作消失。 |
| F13-04 | P2 | 确认 | 权限目录失败显示“已选 0/0”；勾选回写只保留已加载 code，会静默丢权限。 |
| F13-05 | P3 | 确认（修正范围） | 审计摘要“还有 N 项”只数“字段没中文名”，不数“值被隐藏”；对象/未知枚举被跳过。 |
| F13-06 | P3 | 确认 | `AgentInsight` 只显示前 10 行，第 11 条以后无提示。 |
| F13-07 | P3 | 确认 | `LazyRegion` 模块缺 `default` 时 `Loaded=undefined`，区域静默空白。 |
| F13-08 | P3 | 确认 | 全局搜索浮层只跟 resize 不跟 scroll；resize effect 每次渲染重建。 |
| F13-09 | P3 | 确认 | `MaskedContact` 展开态不随 `value` 复位，行复用会把新号码直接展开。 |
| F13-10 | P3 | 确认 | `FollowUpModal.reset()` 不重置 `followupType`，下一条跟进继承上一条方式。 |
| F13-11 | P3 | 确认 | 未读计数失败静默当 0，铃铛不显示角标。 |
| F13-12 | P3 | 确认 | 回款凭证下载是无 `href` 的 `<a onClick>`，键盘不可达。 |
| F13-13 | P3 | 确认（潜伏） | 时间线来源类型不在四类映射内会拼出 `/undefined/{id}`；当前后端称固定四类。 |
| F13-14 | P3 | 确认 | 自定义角色代号原样显示英文 code，违反“界面不出现英文原词”。 |
| **F13-15** | **P3** | **新增确认** | `DecisionMakerCard` 没传 `masked`，后端遮罩号码仍被拼进 `tel:`/`mailto:`。 |
| F13-T01 | — | **证伪（不可达）** | `payment`/`approval` 只是通知 `type`；实际链接按 `business_type` 走 `order`/`quote`，两条映射当前无来源、不会打开错记录。 |

## 二、P1 复核

### F13-01 明文默认密码

`AppLayout.tsx:291-297`：

```tsx
<Modal title="你的登录账号已生成" visible={Boolean(user?.initial_password_pending && credentialsDismissed !== user?.id)} ...>
  <p>登录名：<strong ...>{user?.username}</strong></p>
  <p>初始密码：<strong>123456</strong></p>
  ...
</Modal>
```

只要 `initial_password_pending` 为真，任何能看到该账号屏幕的人都能拿 `123456` 登录；录屏、截图、共享屏幕都会带走。它和 B11 的“首登默认密码”同根，后端没改之前前端至少不该把密码做成大号加粗明文。

确认 P1。改法：一次性激活链接/随机初始口令，或至少默认隐藏并“点击查看一次”，同时强制首次登录改密。

## 三、P2 复核

### F13-02 联系人失败当空

`DecisionMakerCard.tsx:61-78、124-145`：

```tsx
const contactsQuery = useQuery({
  queryKey: ['contacts', customerId],
  queryFn: () => listContacts(customerId!),
  enabled: Boolean(customerId),
})
...
const contacts = useMemo(() => [...(contactsQuery.data ?? [])].sort(...), [contactsQuery.data])
...
contactsQuery.isLoading ? 加载中 : contacts.length === 0 ? (
  <div>该客户还没有登记联系人。...</div>
  <Button onClick={() => navigate(`/customers/${customerId}`)}>去客户详情加联系人 →</Button>
) : ...
```

全组件没有 `contactsQuery.isError`。`listContacts` 500/网络失败时 `data` 为空，页面落“没有联系人”，还引导用户去客户详情“加联系人”——用户可能因此重复建联系人，或者误判客户没有决策人。

确认 P2。改法：失败分支单独显示“联系人读取失败，重试”，不要复用空态。

### F13-03 Copilot 详情失败静默空会话

`CopilotDrawer.tsx:120-128、269-278、389-405`：

```tsx
const detailQuery = useQuery({ ... })
const detailMessages = detailQuery.data?.messages ?? []
const pending = (detailQuery.data?.actions ?? []).filter(...)
...
{messages.length === 0 && !sending && (<快捷提问>)}
```

没有 `detailQuery.isError`。会话详情失败时消息和待确认动作都变空，只剩快捷提问；用户看不到之前聊到哪、也看不到待确认的写操作，容易重复发起同一件事。

确认 P2。改法：详情失败显示“会话读取失败 + 重试”，并保留“正在加载/失败”状态，不要用空会话冒充新会话。

### F13-04 权限目录失败 0/0 + 静默丢权限

`SettingsPage.tsx:989-994、2535-2545`：

```tsx
const permissionsQuery = useQuery({
  queryKey: ['settings-permissions'],
  queryFn: listPermissions,
  enabled: roleModal,
})
...
<PermissionPicker
  loading={permissionsQuery.isLoading}
  value={roleForm.permission_codes}
  onChange={(codes) => setRoleForm({ ...roleForm, permission_codes: codes })}
  options={(permissionsQuery.data ?? []).map(...)}
/>
```

没有 `permissionsQuery.isError`。目录失败时 `options=[]`，选择器顶部显示“已选 0 / 0”，看上去像这个角色没有任何权限。

更严重的是 `PermissionPicker.tsx:151-168` 的 `toggle`/`toggleGroup`：

```tsx
const toggle = (code: string, checked: boolean) => {
  const next = new Set(value)
  if (checked) next.add(code)
  else next.delete(code)
  onChange(options.filter((option) => next.has(option.code)).map((option) => option.code))
}
```

回写只保留“当前 options 里存在”的 code。如果权限目录只加载了一部分，或加载失败后用户又勾了一项，原本已选但不在 options 的 code 会被静默丢掉；保存后角色权限被削减，界面不会提示。

确认 P2。改法：目录失败禁用勾选/保存并提示重试；回写改成 `[...next]` 按原顺序保留，不要用 `options.filter` 做白名单。

## 四、P3 复核

### F13-05 审计摘要少报

`audit-labels.ts:316-346、445-456、488-493`：

```ts
if (typeof value === 'object') return ''   // 对象值被跳过
...
if (/^[a-z][a-z0-9_]{1,24}$/.test(text) || /^[A-Z]{2,6}$/.test(text)) return ''
...
if (!auditFieldLabel(type, key)) hidden += 1
```

`changeSummary` 对 `oldText === '' || newText === ''` 直接 `continue`，所以对象值和未知英文码值不会出现在摘要里；`auditHiddenFieldCount` 只统计“字段名没有中文名”，不统计“字段有中文名、值被隐藏”，于是“还有 N 项”少报。

修正原报告：数组走 `return \`${value.length} 项\``，不是空串，不会少报。确认 P3（对象/未知枚举）。

### F13-06 只显示前 10 行

`AgentInsight.tsx:136-166、201-206`：`rows.slice(0, 10)` 后直接渲染，没有“共 N 条/还有 N 条”的提示。确认 P3。

### F13-07 LazyRegion 缺 default 静默空白

`LazyRegion.tsx:96-102、157-164、250-253`：

```tsx
setLoaded(() => mod.default)
setPhase('ready')
...
{Loaded ? createElement(Loaded, ...) : null}
```

`mod.default` 不是函数时 `Loaded` 为 `undefined`，`phase` 仍是 ready，最终渲染 `null`；区域边界不报错、不降级，用户只看到一块空白。确认 P3。

### F13-08 搜索浮层不跟滚动

`GlobalSearch.tsx:49-56、74-79、164-181`：浮层是 `position: fixed`，坐标只在 focus/change/resize 时 `syncPanel`；没有 `scroll` 监听。页面滚动后输入框移动，浮层留在旧位置。`useEffect` 没有依赖数组，每次渲染都移除再添加 resize 监听。

确认 P3。改法：监听 `scroll`（捕获阶段）和布局变化，resize effect 加 `[open]` 依赖。

### F13-09 展开态不复位

`MaskedContact.tsx:55、88-99`：`revealed` 只在点击时切换，`value` 变化没有 `useEffect(() => setRevealed(false), [value])`。列表行复用、号码从 A 换 B 时，B 一出现就是展开态。

确认 P3。改法：`useEffect` 复位，或列表行 `key` 绑定号码。

### F13-10 跟进方式继承

`FollowUpModal.tsx:31、46-53`：

```tsx
const [followupType, setFollowupType] = useState('电话')
const reset = () => {
  setContent('')
  setFeedback('')
  setNextAction('')
  setExemptionReason(null)
  submission.current = null
  setTaskDue(null)
}
```

`reset()` 没有 `setFollowupType('电话')`。上一条选了“拜访”，下一条默认还是“拜访”，用户不注意会把电话跟进记成拜访。

确认 P3。改法：`reset()` 增加 `setFollowupType('电话')`；若要记住上次方式，应在表单上明示“沿用上次方式”。

### F13-11 未读计数失败当 0

`NotificationBell.tsx:51-55、107-108`：

```tsx
const countQuery = useQuery({ queryKey: ['notifications-unread'], queryFn: getUnreadCount, refetchInterval: 60_000 })
const count = countQuery.data?.count ?? 0
```

没有 `countQuery.isError`。500/网络失败时 `data` 空，`count=0`，铃铛无角标，用户以为没有新通知。确认 P3。

### F13-12 凭证下载键盘不可达

`PaymentVoucherControl.tsx:54-66`：

```tsx
<a style={{ color: 'var(--crm-primary)' }} onClick={() => void showDownloadResult(downloadPaymentVoucher(payment.id, filename))}>
  {filename}
</a>
```

无 `href`，不可聚焦，Tab 到不了；回款凭证是关键钱操作，键盘用户无法下载核对。确认 P3。

### F13-13 时间线 `/undefined`

`Timeline.tsx:73-77`：

```tsx
<Link to={`/${{ sample: 'samples', order: 'orders', quote: 'quotes', opportunity: 'opportunities' }[event.source.type]}/${event.source.id}`}>
  查看原单
</Link>
```

映射不到时 base 为 `undefined`，链接变 `/undefined/123`。当前后端注释称来源固定四类，属潜在缺口，不是已复现。确认 P3（潜伏）。

### F13-14 自定义角色英文 code

`AppLayout.tsx:270-272`：

```tsx
{(user?.roles ?? []).map((role) => ROLE_LABEL[role] ?? role).join(' / ') || '无角色'}
```

`ROLE_LABEL` 只覆盖内置角色；角色管理允许新建自定义角色（`SettingsPage.tsx` 的 `createRole`/`roleForm.code`），顶栏就会显示 `custom_role_code`。确认 P3。

### F13-15 新增：遮罩号码仍可拨号/发邮件

`DecisionMakerCard.tsx:190-250`：

```tsx
<MaskedContact value={primary.email} kind="email" empty="无邮箱" />
<MaskedContact value={primary.mobile ?? primary.phone} empty="无电话" />
...
<Button disabled={!primary.email} onClick={() => { window.location.href = `mailto:${primary.email}` }}>发送邮件</Button>
<Button disabled={!(primary.mobile ?? primary.phone)} onClick={() => { window.location.href = `tel:${primary.mobile ?? primary.phone}` }}>拨打电话</Button>
```

后端 `serialize_contacts_masked` 在没有完整联系方式权限时返回 `138****8000` 并带 `contact_masked=true`；组件没传 `masked={...}`，按钮只判字符串 truthy，于是遮罩号码仍可点“拨打电话/发送邮件”，实际拨到星号、发到星号邮箱。

确认 P3（新增漏项）。改法：传 `masked`，遮罩时禁用拨号/邮件/企微复制；或按钮使用后端返回的 `contact_masked` 判断。

## 五、F13-T01 证伪：当前不可达

`NotificationBell.tsx:20-31` 的映射是：

```tsx
order: (id) => `/orders/${id}`,
quote: (id) => `/quotes/${id}`,
approval: (id) => `/quotes/${id}`,
payment: (id) => `/orders/${id}`,
```

但实际通知从不把 `business_type` 写成 `payment` 或 `approval`：

- `payment/router.py:1190-1199`：回款确认通知 `type_="payment"`，但 `business_type="order"`、`business_id=order.id`；
- `approval/router.py:360-377、406-415、461-469、577-584`：审批通知 `type_="approval"`，`business_type="quote"`、`business_id=quote.id`；
- 铃铛按 `business_type` 取映射，`payment`/`approval` 只是 `type`，不参与链接。

所以 `approval`/`payment` 两条映射是死分支，当前不会产生任何跳转，更不会打开错记录。**F13-T01 证伪，不计缺陷。** 只有未来新增 `business_type="payment"`/`"approval"` 的通知时才需要补映射，届时按潜伏项处理。

## 六、本块最大漏项

**F13-15**：原报告完全没有记这条，只在通用组件清单里提了 `MaskedContact`，没有把它和 `DecisionMakerCard` 的拨号/邮件按钮连起来。实际后端遮罩后返回的是带星号的字符串，按钮照样可用，`tel:`/`mailto:` 会拿星号去拨号/发邮件——这是可复现的功能错误，不是纯展示问题。

若只算原报告已有条目，最大风险仍是 **F13-01 明文默认密码**；F13-04 权限目录失败后勾选丢 code 是第二个容易造成真实权限削减的点。

## 七、残余

- F13-T01 当前不可达，已证伪；后端未来若新增 payment/approval 类型通知，需要重新检查映射。
- F13-13 依赖后端时间线来源是否严格固定四类；当前按潜伏处理。
- F13-05 的对象值/未知枚举是展示少报，不影响审计明细原文；若审计取证依赖摘要完整性，建议补“其他 N 项变更”。
- 本轮只改 `review/`，未改任何业务代码；`review/repro/f13_frontend/check_f13.mjs` 结果为 `16 confirmed/pending, 0 missing`。
