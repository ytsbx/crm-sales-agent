# 前端两条 P0 端到端复核

结论：**F01-01、F10-01 都成立，不是“前端露个按钮、后端其实拦住”的假 P0。**

## 一、F01-01 已转订单仍可再建版本、再转一张订单

### 前端仍给入口

`QuoteDetailPage.tsx` 的按钮条件：

```tsx
'新建版本': Boolean(canManage && !rejection),
```

没有判断 `quote.status !== 'converted'`。转单后：

- `quoteTodos` 命中 `converted` 分支，`currentStage` 为 `converted`；
- `quoteActions('converted')` 的 primary/secondary 都为空；
- 但页面后面这段仍会把“新建版本”拿出来：

```tsx
for (const label of ['新建版本', '下载 PDF']) {
  const element = take(label)
  if (element) rendered.push(element)
}
```

所以已转订单的报价详情页，右上角仍有“新建版本”。

### 后端也不拦

`POST /quotes/{quote_id}/versions` 只做可见性检查和 `quote:manage` 权限，没有 `quote.status == 'converted'` 判断；`svc.create_version` 在复制完明细后直接：

```python
quote.current_version_id = version.id
quote.status = "draft"
await close_superseded_approvals(...)
```

也就是说，转单后被置为 `converted` 的报价，会被新版本强行改回 `draft`，原订单仍挂在这个报价上。

### 第二张订单怎么出来

`POST /quote-versions/{version_id}/convert-to-order` 的查重是：

```python
existing = select(SalesOrder).where(SalesOrder.quote_version_id == version.id)
```

只按**版本 id** 查重。新版本是新 id，查不到旧订单；`ensure_current_version` 又因为 `current_version_id` 已切到新版本而通过。于是同一份报价可以生成第二张正式订单和第二条应收。

**确认 P0。** 修法：`create_version` 先拒绝 `converted` 报价（或要求先走订单取消/作废）；前端同时隐藏入口。只在按钮上加 `disabled` 不够，Agent 工具 `create_quote_version` 也走同一 service。

## 二、F10-01 企微候选/建客/绑客绕过客户数据范围

### 候选接口只校验 `wecom:view`

`wecom/router.py:250-257`：

```python
@router.get("/integrations/wecom/unbound-contacts/{contact_id}/candidates")
async def unbound_candidates(
    contact_id: int,
    _: CurrentUser = Depends(require_permission("wecom:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.unbound_candidates(session, contact_id))
```

`svc.unbound_candidates` 明确写：

```python
# 这里**故意不做数据范围过滤**（不传 owner_ids）
matches = await find_duplicate_customers(session, company_name=..., mobile=None)
```

`find_duplicate_customers` 在 `owner_ids=None` 时查全库，返回：

```python
{"id", "name", "level", "region", "owner_id", "pool_status", "score", "reasons"}
```

所以只有 `wecom:view`、没有对应客户数据范围的账号，可以按外部联系人企业名/姓名枚举全公司客户名称、等级、地区、负责人、公海状态。

### 前端候选卡不看客户权限

`WeComPage.tsx:188-191`：

```tsx
const candidatesQuery = useQuery({
  queryKey: ['wecom-candidates', activeId],
  queryFn: () => getUnboundCandidates(activeId!),
  enabled: Boolean(activeId),
})
```

只按 `activeId` 开启；候选卡在 `candidatesQuery.data` 回来后无条件渲染，没有 `customer:view` 或数据范围门。`canManage` 只控制“关联已有客户”按钮，不控制候选列表本身。

### 建客/绑客也只校验 `wecom:manage`

`bind-customer`、`create-customer` 都是：

```python
Depends(require_wecom_action("wecom:manage"))
```

`bind_customer` 里取客户用 `customer_service.get_customer_or_404(session, customer_id)`，没有 `ensure_in_scope`；`create_customer_from_contact` 建客也只按 `wecom:manage`。因此有 `wecom:manage` 的账号可以把外部联系人挂到任意客户 id，甚至不需要 `customer:view`。

**确认 P0。** 这与 B15-01/B15-02 同根：企微是独立权限域，但没有叠加客户数据范围。修法至少三处：候选查询传 `scoped_owner_ids`（公海仍可参与）；候选接口要求 `customer:view` 或按范围过滤；绑客前 `ensure_in_scope`，建客沿用客户创建权限。

## 三、结论

- F01-01：前端露出 + 后端 `create_version` 不判 `converted` + 转单只按版本 id 查重，三条链闭合，P0 成立。
- F10-01：`wecom:view` 候选全库匹配 + 前端无客户权限门 + 绑客无数据范围，P0 成立。
- 两条都不是“只改前端按钮”能解决的；F01 要后端 service 拒转单后建版，F10 要后端候选/绑客叠加客户范围。
