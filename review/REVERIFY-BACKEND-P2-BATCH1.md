# 后端 P2 复核（第一批：13 条“已修复”声明）

> 目的：逐条验证“后端说已经修好了”的 P2。只看当前代码和可执行函数，不引用进度记录。
> 日期：2026-11-24。只审查，未改业务代码。

## 一、结果

13 条“已修复”声明中：

- **12 条能从当前代码看到修复**
- **1 条仍成立：B20-06**

所以“13 条全部修好”这个说法不成立。

## 二、逐条证据

| 编号 | 声明 | 当前代码复核 | 结论 |
|---|---|---|---|
| B08-02 | PDF 页脚普通文本已转义 | `contract/pdf.py:9` 引入 `xml.sax.saxutils.escape`，`:29` 统一转义；`bizdoc/pdf.py:101` 同样 | 已修 |
| B08-03 | 单据模板备注 255 字上限 | `bizdoc/router.py:50` `remark: str | None = Field(max_length=255)`，创建入口用它 | 已修 |
| B09-03 | 审批定义名称/说明列宽校验 | `approval/rules_router.py:52` 名称 `max_length=128`；`:58` 说明 `max_length=255`，与模型列宽一致 | 已修 |
| B11-09 | 旧钉钉模板坏 JSON/缺控件不再裸抛 | `sample/dingtalk.py:100-104` 对 JSON 解析失败转 `EXTERNAL_ERROR` 502；`:107-140` 逐项校验 `schemaContent/items/props/id/options`，缺项抛 `ValueError` 后统一转模板异常 | 已修 |
| B17-02 | 0.001 数量/单重不再走金额舍入 | `agent/tools.py:182-194`：金额 `_money` 两位 HALF_UP，重量/体积 `_measure` 四位 HALF_UP；数量入口直接用 `Decimal`，不再套金额两位 | 已修（出口仍有展示层两位，属另一条） |
| B17-07 | 物流 AI 入口数量按物流模型转 | `agent/tools.py:1560-1584` 构造 `LogisticsCalculateRequest(sku_id, quantity)` 再调 `logistics_service.prepare`；非法数量在构造处报错，不再把 float 直接塞进物流服务 | 已修 |
| B19-04 | 价格导入异常不泄露数据库原文 | 共用 `core/importing.py:317-322` `write_failed_row`：只记日志异常类型，回给用户固定“该行保存失败，请重试；仍失败时联系管理员并提供行号” | 已修 |
| B20-05 | SKU 导入数值约束与主数据共用 | `product/io.py:144-180` `sku_fields_from_row` 逐字段 `_text/_sku_number`，MOQ 整数/非负、重量/箱规范围在解析阶段拒绝；`product/io_router.py:349-365` 先收集 `errs` 再落行 | 已修 |
| B20-06 | 外部规格 201 字符采纳前按列宽校验 | **未修**：`product/master.py:185-193` `_text()` 只 `strip()`；`normalize_field_value` 文本字段 `:215-216` 只 `str(value).strip()`。实跑 `normalize_field_value('name','x'*300)` 返回 `len=300`，而 `product/model.py:68` 列宽 200 | **仍成立** |
| B21-02 | 品名/备注/驳回原因/承运商/运单号入参上限 | `sample/schema.py`：品名 200、备注 255/1000、驳回原因 255、承运商/运单号 64；`order/schema.py` 备注 255、物流公司/运单号 64 | 已修 |
| B21-03 | 价格导入异常统一脱敏 | 同 B19-04，共用 `ImportReport.write_failed_row` 固定提示，不拼数据库异常原文 | 已修 |
| B22-04 | 打样新版复制旧申请字段 | `sample/router.py:780-805` 修订版复制 `purpose/target_completion_date/acceptance_criteria/sample_fee/production_owner_id/sample_type/product_category/maker_unit/estimated_hours/craft_points/sample_purpose/urgency/sample_situation/deliver_date/sample_maker/material_spec/product_size/printing/accessories/packaging/craft_requirement/product_name` | 已修 |
| B23-03 | 导入提交异常不回显数据库原文 | 同 B19-04/B21-03，共用 `ImportReport.write_failed_row` 脱敏 | 已修 |

## 三、B20-06 的实测

```text
from app.modules.product.master import normalize_field_value
v = normalize_field_value('name', 'x' * 300)
len(v) = 300
product/model.py:68  name 列宽 200
```

这条不是“展示问题”：主数据核定把外部来源的 `name/specification/color/material/package_type/unit` 直接 `setattr` 到 ORM，超长会到 `flush()` 才由数据库抛 `value too long`，接口 500；且差异证据里的原值可能已被截断/丢失。原报告说“采纳前按列宽校验，保留差异证据”，当前代码没有这个校验。

## 四、结论

- `QUALITY_PROGRESS` 里的“P2 13 条已修”应改为 **12 条已修、B20-06 仍确认**。
- 下一批继续 P2 剩余的权限/范围、幂等、数据完整性条目；这批只完成了“已修声明”的验真，不等于 P2 全量结束。
