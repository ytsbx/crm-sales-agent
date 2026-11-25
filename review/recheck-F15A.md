# F15-A 全局样式 [1-5000] 复核：5 条确认（1 条升级、1 条降级）

- 范围：`frontend/src/index.css` 第 1–5000 行；复核时实际文件 **8500 行**，不是审查报告的 8487 行——工作区在当前 CSS 第 2651 行后新增了 13 行「可搜索 Select 内部输入框」规则，旧行号整体后移。
- 方式：逐条重读当前 CSS 与调用点；`review/repro/f15_frontend/check_f15a.mjs` 做静态断言、锚点计数和高度算术；没有改任何业务代码。
- 命令：`node review/repro/f15_frontend/check_f15a.mjs`
- 结果：**原报告 4 条全部成立；原“待确认”F15A-T01 落为确认；F15A-03 由 P2 降 P3。0 条误报。**
- 一处修正：全仓 `<a>` 是 101 个、带 `href` 的是 **3 个**（不是 2 个）；其中 `CasesPage` 的 `href="#"` 只是为了让标题可聚焦，另 2 个是真实外链。无 `href` 的仍有 98 个。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F15A-01 | P2 | 确认 | 98 个无 `href` 的 `<a>` 键盘不可聚焦，CSS 只补了手型和颜色。 |
| F15A-02 | P2 | 确认 | 全站表体 `nowrap + overflow:hidden`，固定宽度金额列截断且无完整值兜底。 |
| F15A-03 | P3（原 P2） | 确认降级 | 全站表格 `center !important` 压掉 `align:right`，What-if 七个数值列仍居中；不隐藏数据，降 P3。 |
| F15A-04 | P3 | 确认（潜伏） | 46 处 `:has()` 承载布局/隐藏逻辑，旧浏览器整条丢弃。 |
| F15A-T01 | P3 | 确认（原待确认） | toast `top:84px` 低于顶栏 56 + 页签条最低 34 = 90px，至少压住页签下沿 6px。 |

## 二、P2 复核

### F15A-01 无 href 的 `<a>` 键盘不可聚焦

`index.css:204-218`：

```css
a:not([href]) {
  cursor: pointer;
  color: var(--crm-primary);
}
```

当前 TSX 计数：

```text
<a> 101 个
带 href 3 个（2 个外链 + 1 个 href="#"）
带 onClick 35 个
带 role 0 个
带 tabIndex 0 个
```

无 `href` 的 `<a>` 浏览器不给 Tab 焦点，也不响应 Enter/Space；CSS 只能改鼠标手型和颜色，补不出语义。98 个入口里包含下载凭证、恢复客户、详情、编辑等关键动作，键盘用户直接跳过。

确认 P2。修法：统一换 `Button`/原生 `button`，或补 `role="button" tabIndex={0}` + Enter/Space；不要继续把无 `href` 的 a 当按钮。

### F15A-02 金额列被单行省略截断

`index.css:2067-2071`：

```css
.semi-table-tbody .semi-table-row-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

`OrderListPage.tsx` 的订单金额列：

```tsx
{ title: '订单金额', dataIndex: 'total_amount', className: 'cell-right', width: compact ? 108 : 124,
  render: (v: number, record: Order) =>
    formatMoney(toTwoDecimals(record.total_amount_exact ?? v), record.currency),
}
```

124px（compact 108px）扣掉单元格内边距，可用宽度不到 100px；`formatMoney` 直接返回字符串，没有 `title`/展开入口。`¥1,234,567.89` 以上就会被省略号裁掉。`.qst-amount` 是另一端：`nowrap` 但不 `overflow:hidden`，超大金额会撑出卡片。

确认 P2。修法：金额列不套全站省略号规则，或列上 `ellipsis` + `title`；更好的做法是保证最小列宽、表格容器横向滚动，不要把数字裁掉。

## 三、P3 复核

### F15A-03 全站表格强制居中（原 P2，降 P3）

`index.css:2540-2542`：

```css
.semi-table-thead > .semi-table-row > .semi-table-row-head,
.semi-table-tbody > .semi-table-row > .semi-table-row-cell {
  text-align: center !important;
}
```

`WhatIfPanel.tsx` 的七个数值列都写 `align: 'right' as const`，但 Table 没挂 `cell-right`：

```tsx
{ title: '需求条数', dataIndex: 'item_count', width: 90, align: 'right' as const },
{ title: '报价金额', dataIndex: 'amount_total', width: 120, align: 'right' as const },
...
<Table columns={versionColumns} dataSource={versions} rowKey="version_id" pagination={false} />
```

`center !important` 压掉列上的右对齐，金额/毛利率仍居中。数据没被隐藏，只是横向比较时小数点不对齐；降 P3。

修法：全局规则改成只居中文本列，或金额列统一用会自带 `cell-right` 的列工厂；至少给 WhatIf 七列补 class。

### F15A-04 46 处 `:has()`

当前 CSS 实测 46 处 `:has(`。不支持的浏览器（Chrome 105 以下、Safari 15.4 以下、Firefox 121 以下）会整条丢弃，例如：

```css
.page-container:not(:has(.card-block ~ .card-block)) > .card-block { flex: 1 1 auto; }
.semi-table-wrapper:has(.semi-table-placeholder) .semi-table-header { overflow-x: hidden; }
```

表现为单卡不拉满、空表又出现滚动条、部分禁用提示不生效。纯视觉，确认 P3（潜伏）。

### F15A-T01 toast 压页签（原待确认，落 P3）

`index.css:39-41`：`top: 84px !important`。

静态最低高度：

```text
顶栏 .app-header 56px
页签条 .tab-strip padding-top 8 + border 1 + .tab-open padding 6*2 + 字号 13 = 34px
合计 90px
```

toast 从 84px 开始，至少压住页签下沿 6px；如果页签换行/字号放大，压得更多。确认 P3（原待确认已落地）。修法：用 `calc(var(--crm-header-h) + var(--crm-tabstrip-h) + 8px)`，由布局注入实际高度。

## 四、本块最大漏项

本块没有新缺陷，最大未解决项是 **F15A-02**。审查员把它记成“订单金额列截断”，但根因是**全站表体统一 `overflow:hidden`**：任何金额列只要没给足宽度、没给 `title`，都会中招，不只是订单列表。只改订单列宽，下一张表还会复发。

F15A-03 与 F15A-02 同根：全站规则用 `!important` 统一表格表现，调用点必须逐个挂 `.cell-right` 才能拿回对齐。真正该收口的是“列工厂/金额列统一 class”，不是继续补单点。

## 五、残余

- 没有浏览器实测，金额截断是静态宽度推断；`¥1,234,567.89` 以上确定会超 100px，具体从第几位开始省略取决于字体。
- F15A-04 是否触发取决于目标浏览器矩阵；当前代码没有 `@supports` 降级。
- F15A-T01 按最低高度 90px 推算；实际页签条可能更高，重叠只会更多。
- 本轮只写 `review/`，未改业务代码。`check_f15a.mjs` 结果 `5 confirmed, 0 missing`。
