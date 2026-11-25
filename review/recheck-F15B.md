# F15-B 全局样式 [5001-8487] 复核：5 条确认（1 条降级）

- 范围：`frontend/src/index.css` 第 5001 行到文件末尾；复核时实际文件 **8500 行**，审查报告写 8487 行，原因是当前工作区在第 2651 行后新增了 13 行「可搜索 Select 内部输入框」规则，5001 之后的行号整体 +13。
- 方式：逐条重读当前 CSS 与调用点；`review/repro/f15_frontend/check_f15b.mjs` 做静态断言；没有改任何业务代码。
- 命令：`node review/repro/f15_frontend/check_f15b.mjs`
- 结果：**原报告 5 条全部成立；F15B-03 由 P2 降 P3。0 条误报。**
- 行号说明：下文按**当前文件**给行号；审查报告里的 5241/7252/7690 等旧行号，在当前文件里整体向后约 13 行。

## 一、结论表

| 编号 | 级别 | 结论 | 一句话 |
|---|---|---|---|
| F15B-01 | P2 | 确认 | `<1200` 用 `display:none` 直接隐藏“已收/未收/已分配/待分配”金额列。 |
| F15B-02 | P2 | 确认 | 弹窗自定义样式把 Semi 错误态强改成白底蓝边，校验失败红框消失。 |
| F15B-03 | P3（原 P2） | 确认降级 | 禁用实心主按钮仍白底蓝字，看起来可点；禁用拦截仍在，降 P3。 |
| F15B-04 | P3 | 确认 | 分析页漏斗固定 404px、四列固定 300px，320 宽溢出。 |
| F15B-05 | P3 | 确认（潜伏） | 危险链接红色只认 3 个内联字符串，换色/换 class 就变回普通蓝。 |

## 二、P2 复核

### F15B-01 窄屏直接隐藏金额列

`index.css:5295-5299`：

```css
@media (max-width: 1199px) {
  .col-hide-narrow {
    display: none !important;
  }
}
```

`OrderDetailPage.tsx:871-872、1433-1434`：

```tsx
{ title: '已收', dataIndex: 'received_amount', width: 130, className: 'cell-right col-hide-narrow', ... },
{ title: '未收', dataIndex: 'remaining_amount', width: 130, className: 'cell-right col-hide-narrow', ... },
{ title: '已分配', dataIndex: 'allocated_amount', width: 108, className: 'cell-right col-hide-narrow', ... },
{ title: '待分配', dataIndex: 'unallocated_amount', width: 108, className: 'cell-right col-hide-narrow', ... },
```

1024 笔记本/分屏命中 `<1200`：

- 应收计划只剩“应收金额 + 状态”，看不到已收/未收；
- 回款记录只剩“整笔到账”，看不到已分配/待分配；
- 用户可能把“状态=已确认”误当成“已收清”，或以为回款已全部分配。

不是横向滚动，也不是折叠，是整列删除。确认 P2。

修法：金额列不要 `display:none`；改横向滚动，或把“已收/未收”合并两行、把“已分配/待分配”压在整笔到账下面。若业务明确允许窄屏牺牲，页面上要写明“窄屏已隐藏金额列”。

### F15B-02 弹窗错误态被覆盖

`index.css:7278-7289`：

```css
.semi-modal .semi-input-wrapper-focus,
.semi-modal .semi-input-wrapper-error,
.semi-modal .semi-input-textarea-wrapper-focus,
.semi-modal .semi-select-focus,
.semi-modal .semi-select-open,
.semi-modal .semi-datepicker-focus {
  background: #fff !important;
  border-color: var(--crm-primary) !important;
  box-shadow: 0 0 0 3px rgba(47, 109, 246, .12);
}
```

基础规则 `.semi-modal .semi-input-wrapper` 是 2 个类，已经压过 Semi 的 `.semi-input-wrapper-error`（1 个类）；错误态又和 focus 同组，被强设成白底主色边。`.semi-modal .form-card .semi-input-wrapper` 还是一条 3 类白底灰边规则。

结果：提交空必填、非法金额后，字段已经 `semi-input-wrapper-error`，视觉上仍是正常灰底/蓝边；用户以为没报错，继续填错。确认 P2。

修法：补更高优先级的错误态：

```css
.semi-modal .semi-input-wrapper.semi-input-wrapper-error {
  background: var(--crm-error-soft);
  border-color: var(--crm-error);
}
```

聚焦时保持红边/红光晕；select/datepicker/textarea 同理。不要让 focus 覆盖 error。

## 三、P3 复核

### F15B-03 禁用实心主按钮仍像可用（原 P2，降 P3）

`index.css:7698-7706`：

```css
.semi-button-primary.semi-button-solid {
  background: #fff !important;
  background-image: none !important;
  color: #2f6df6 !important;
  border: 1px solid rgba(47, 109, 246, .5) !important;
  box-shadow: 0 1px 2px rgba(47, 109, 246, .12) !important;
}
```

当前没有 `.semi-button-primary.semi-button-solid.semi-button-disabled` 或 `:disabled` 规则；只有 `.semi-button-primary.semi-button-light...disabled` 和表格内 `.semi-button.semi-button-disabled`。`KnowledgePage.tsx:849`：

```tsx
<Button theme="solid" disabled={saveMutation.isPending || !can('file:manage') || !can('quote:manage')}>
  选择照片（可多选）
</Button>
```

Semi 的 `Button` 默认 `type='primary'`，所以它落到 `.semi-button-primary.semi-button-solid`。白底蓝字 + 蓝边全带 `!important`，压过 disabled 灰化；只剩 `cursor:not-allowed` 和原生 disabled 阻止点击。

用户会以为按钮可用，点了没反应；在“提交/确认”类按钮上容易误判。禁用本身仍生效，没有造成错误写入，**降 P3**。

修法：补 solid 的 disabled 规则（灰底、灰字、灰边、无投影）；同类覆盖 `quote-actions` 也要带禁用态。

### F15B-04 320 宽漏斗/四列溢出

`index.css:5520-5525`：

```css
.deal-flow {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 0 0 404px;
  min-width: 0;
}
```

`index.css:7174`：

```css
@media (max-width: 1180px) {
  .deal-table-head, .deal-table-row { grid-template-columns: minmax(0, 1fr) 72px 96px 96px; gap: 12px; }
}
```

320 宽去掉卡片左右内边距约 280px，404px 流程条必溢出；四列固定部分 `72 + 96 + 96 + 3*12 = 300px`，同样顶出容器。页面没有横向滚动壳，表现为右侧被裁或整页横向滚动。确认 P3。

修法：560 以下 `.deal-flow { flex-basis: 100%; }`，四列清单改两列/单列，或整体放进横向滚动壳。

### F15B-05 危险链接红色白名单（潜伏）

`index.css:7749-7754`：

```css
.semi-table-tbody .semi-table-row-cell a {
  color: #2c62d9 !important;
}
```

`index.css:8159-8162`：

```css
.semi-table-tbody .semi-table-row-cell a[style*="crm-error"],
.semi-table-tbody .semi-table-row-cell a[style*="#f53f3f"],
.semi-table-tbody .semi-table-row-cell a[style*="#ff4d4f"] {
  color: var(--crm-error) !important;
}
```

当前表格红链接基本都写 `var(--crm-error)`，所以现状没漏网。但只要改用 class、`var(--crm-danger)`、`#d4380d`、`#dc2626`，或被组件转成普通 class，就会被全局蓝 `!important` 压掉，删除/作废重新变成“安全蓝”。确认 P3（潜伏/维护风险）。

修法：危险操作统一 `.cell-danger-link` 类或 `Button type="danger"`，不要用内联 style 原文做白名单。

## 四、本块最大漏项

原报告 5 条都成立，但漏了一个**系统性根因**：F15B-02 和 F15B-03 不是两条孤立样式 bug，而是同一套“用 `!important` 覆盖 Semi 组件状态”的做法——错误态、focus、disabled 都被自定义规则压掉。只修弹窗红框和 solid 禁用，下一处 `loading`/`selected`/`warning` 还会复发。

真正该收口的是：自定义主题只覆盖**静止态**，状态类（error/disabled/loading）要么留给 Semi，要么按状态单独补规则，并且禁止用 `!important` 压状态。

## 五、残余

- 没有浏览器渲染实测；F15B-01 是 1024 宽度下确定命中媒体查询，F15B-02/03 是选择器优先级确定，F15B-04 是固定像素算术确定。
- F15B-05 当前未发现漏网红链接，按“新增/换色即失效”的维护风险记 P3，不定成现网缺陷。
- 当前 CSS 8500 行，比审查报告多 13 行；多出的是第 2651 行后“可搜索 Select 内部输入框”规则，未改变本块结论；本轮没有审计这 13 行的业务效果。
- 本轮只写 `review/`，未改业务代码。`check_f15b.mjs` 结果 `5 confirmed, 0 missing`。
