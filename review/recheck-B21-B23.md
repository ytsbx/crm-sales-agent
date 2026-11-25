# B21–B23 高严重度复核（证伪）

- 范围：B22 的 3 条 P1、B23 的 1 条 P1；B21 报告自身 0 P1，本批确认其分布。P2/P3 未逐条复核。
- 方法：重读打样负责人校验、本地审批、PATCH 白名单与 `PART_LOCK_FIELDS`、审批分级校验与角色闸门；纯函数可测的直接跑。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b21_b23_highlights.py -q`
- 结果：`5 failed`，均为“修复后应有”的断言失败。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B21 | **0 P1 成立** | 报告列 4 条 P2 + 9 条 P3，无 P1 |
| B22-01 P1 | **确认** | `_assert_active_owner` 只判存在 + active，不判数据范围；`sample:manage` 可把 `owner_id`/`production_owner_id` 指到范围外用户，打样单立刻归其名下 |
| B22-02 P1 | **确认** | `approve_sample` 只要求 `sample:manage`，不比较 `created_by` 与 `user.id`；非管理员可自建自批 |
| B22-03 P1 | **确认，且比审查员写的更宽** | `PART_LOCK_FIELDS` 只有 `target_completion_date`/`acceptance_criteria`；`sample_fee`、`estimated_hours`、`production_owner_id` 以及单头的 `material_spec/product_size/printing/accessories/packaging/craft_requirement` 都可改，状态仍 approved |
| B23-01 P1 | **确认** | `approval_levels` 只校验 `role_codes` 是 list（空 list 合法）、`max_amount` 只校验格式不限位数；空角色 → `allowed_roles` 为空直接跳过角色限制，100 位整数 → `float` 成 `inf`，所有报价落第一档 |

## 三条需要单独说明

### B22-01：范围外指派不是“看不到客户”，是“单子归他”

打样单的列表/详情可见性只看 `SampleRequest.owner_id` 的 `scoped_owner_ids`（`sample/service.py:222-228、285-324`）。`_assert_active_owner` 只判存在 + `status == "active"`，没有 `scoped_owner_ids`/`ensure_in_scope`。所以一次正常的 PATCH 就能把单子指到范围外用户；该用户即使看不到客户/商机，也能从打样列表看到客户名称、联系人、商机、打样费用与明细。原负责人则可能因“已不归我”而看不到。这是改 ID 越权，不是普通读泄露。

### B22-02：本地审批没有“申请人/审批人”这对角色

`approve_sample` 只做 `ensure_local_review`（挡外部 OA 流程）+ 状态流转，完全不比较 `sample.created_by`/`owner_id` 与 `user.id`。任何 `sample:manage` 都能批自己刚建的单。项目口径是“管理员可审批自己提交的 CRM 内部审批，非管理员仍按原规则”，代码没有区分管理员与非管理员，等于把例外扩到了所有人。

### B23-01：空 role_codes 是“静默放行”，不是“配置少填一项”

`validate_setting_value` 对 `role_codes` 只做 `if role_codes is not None and not isinstance(role_codes, list)`；空 list 通过。到了 `approval/access.py:39`：

```python
allowed_roles = summary.get("node_role_codes") or []
if allowed_roles and not admin and not (set(user.roles) & set(allowed_roles)):
    return "该审批需要…"
```

`allowed_roles` 为空就直接跳过角色校验，任何有 `quote:approve` + 价格审批权限的人都能批这一档。这不是“少配一个角色”，是把“总监/财务”这一档的角色限制整档删掉，而且保存时没有任何提示。

`max_amount` 的 100 位整数能通过 `_strict_amount`（正则只要求数字和最多两位小数），`Decimal` 保留后到 `quote/service.py:2768` 被 `float(max_amount)` 转成 `inf`，`amount <= inf` 对所有金额成立，第一档吃掉全部报价，后面档永远轮不到。

## 本批最大漏项（B22-03 的“车间依据”）

**审查员盯着 `sample_fee`/`estimated_hours`，漏了 `PART_LOCK_FIELDS` 与 `PART_LOCK_LABEL` 的直接矛盾：标签说“材质、工艺、图纸版本、目标完成日、验收标准”，字段只有后两项。**

`PATCH /samples/{id}` 的白名单里有：

- `material_spec`（材质）
- `product_size`（规格）
- `printing`（印刷）
- `accessories`（配件）
- `packaging`（包装）
- `craft_requirement`（工艺要求）

这些都在单头上、都能被 `setattr`，但都不在 `PART_LOCK_FIELDS`。结果：

- 打样单 `approved` 后改材质/规格/印刷，`changed` 非空、`locked=[]`、`_reopen` 不执行，状态仍是 `approved`；
- 车间按新资料做，审批批的是旧资料；
- 若已 `made_at`，`_gate_part_lock` 只在 `locked` 非空时才调用，这些字段根本走不到“已制作不能原地改”的拦截。

审查员把这条写成“改打样费/工时/生产负责人”，实际风险更大的是“改车间依据还不重审”。修的时候不能只把 `sample_fee` 加进锁字段，得让 `PART_LOCK_FIELDS` 与 `PART_LOCK_LABEL` 对齐，并补“已制作后改材质必须开修订版”的用例。

## 后端 P1 覆盖状态

B01–B24 的 P1 已全部过完。P2 只覆盖了 B01–B05；B06–B23 的 P2/P3 尚未逐条复核。也就是说“后端高严重度清完”成立，“后端全部发现清完”还不成立。

## 残余/未覆盖

- B21-01..13、B22-04..12、B23-02..09 的 P2/P3 未逐条复核。
- B22-01/B22-02/B22-03 的复现是源码结构断言；真实权限组合与状态流转用例未搭。
- B23-01 的 `max_amount` 超大值已用纯函数证明可通过；`role_codes` 空值只做了源码断言，未走完整 `set_setting`。
- B23 还有 2 条待确认（定时回收防重入锁、savepoint 回滚），本批未定级。
