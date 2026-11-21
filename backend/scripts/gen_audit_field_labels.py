#!/usr/bin/env python3
"""生成审计页要用的**对象字段清单** —— 从 ORM 元数据导出，不手写。

主人 2026-10-12 拍板「审计页三层都翻干净」（A 方案）。三层里的**字段**那一层
真源在后端 `app/core/patch_schema.py` 的 `FIELD_LABELS`（项目里唯一一张字段
中文名表，前端不再抄第二份 —— 见 `frontend/src/shared/audit-labels.ts` 的说明）。

这个脚本只导出"**每类对象有哪些字段**"这一件事实：界面要靠它判断
"快照里这个字段属于哪类对象"（好给 `name`/`status` 这种同名不同义的字段定名）。
字段清单从 SQLAlchemy 元数据读，**不是手抄**，所以不会跟模型走散。

跑法：`python backend/scripts/gen_audit_field_labels.py`
CI 检查：`ops/check_audit_labels.py` 会核对产物是不是最新的。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
OUT = REPO / 'frontend' / 'src' / 'shared' / 'audit-field-labels.ts'

sys.path.insert(0, str(BACKEND))

#: 审计里出现过的对象类型 → 模型（"有哪些字段"的权威来源）。
#: 只登记**在审计日志里出现过**的对象；没登记的由界面按"其他记录"兜底。
MODEL_OF_TYPE: dict[str, tuple[str, str]] = {
    'order': ('app.modules.order.model', 'SalesOrder'),
    'order_milestone': ('app.modules.order.model', 'OrderMilestone'),
    'quote': ('app.modules.quote.model', 'Quote'),
    'quote_version': ('app.modules.quote.model', 'QuoteVersion'),
    'quote_item': ('app.modules.quote.model', 'QuoteItem'),
    'quote_charge': ('app.modules.quote.model', 'QuoteCharge'),
    'sample': ('app.modules.sample.model', 'SampleRequest'),
    'sample_item': ('app.modules.sample.model', 'SampleItem'),
    'contract': ('app.modules.contract.model', 'ContractDocument'),
    'contract_template': ('app.modules.contract.model', 'ContractTemplate'),
    'biz_doc': ('app.modules.bizdoc.model', 'BizDoc'),
    'biz_doc_template': ('app.modules.bizdoc.model', 'BizDocTemplate'),
    'customer': ('app.modules.customer.model', 'Customer'),
    'contact': ('app.modules.customer.model', 'Contact'),
    'tag': ('app.modules.customer.model', 'Tag'),
    'custom_inquiry': ('app.modules.inquiry.model', 'CustomInquiry'),
    'opportunity': ('app.modules.opportunity.model', 'Opportunity'),
    'opportunity_stage': ('app.modules.opportunity.model', 'OpportunityStage'),
    'lead': ('app.modules.lead.model', 'Lead'),
    'followup': ('app.modules.followup.model', 'FollowUp'),
    'task': ('app.modules.task.model', 'Task'),
    'payment': ('app.modules.payment.model', 'PaymentRecord'),
    'receivable_plan': ('app.modules.payment.model', 'ReceivablePlan'),
    'case': ('app.modules.cases.model', 'SalesCase'),
    'product': ('app.modules.product.model', 'Product'),
    'sku': ('app.modules.product.model', 'Sku'),
    'sku_identity_source': ('app.modules.product.model', 'SkuIdentitySource'),
    'product_cost': ('app.modules.pricing.model', 'ProductCost'),
    'product_insight': ('app.modules.product_insight.model', 'ProductInsight'),
    'price_rule': ('app.modules.pricing.model', 'PriceRule'),
    'customer_price_rule': ('app.modules.pricing.model', 'CustomerPriceRule'),
    'logistics_rate': ('app.modules.pricing.model', 'LogisticsRate'),
    'logistics_quote': ('app.modules.pricing.model', 'LogisticsQuote'),
    'file': ('app.modules.file.model', 'FileRecord'),
    'role': ('app.modules.user.model', 'Role'),
    'user': ('app.modules.user.model', 'User'),
    'department': ('app.modules.user.model', 'Department'),
}

HEADER = '''/**
 * 审计页用的**字段中文名**与**对象字段清单**（脚本生成，别手改）。
 *
 * ⚠️ **这个文件是脚本生成的，不要手改**：
 *     python backend/scripts/gen_audit_field_labels.py
 *
 * 为什么需要它：审计快照里只有一个**裸字段名**，而 `name` / `status` / `title` /
 * `source` 这 4 个字段同名不同义（客户上叫「客户名称」、产品上叫「产品名称」）。
 * 界面要靠这份清单判断"这个字段属于哪类对象"，才能定名而不猜错。
 * 字段中文名的真源在后端 `app/core/patch_schema.py` 的 `FIELD_LABELS`，
 * 那一层由 `shared/audit-labels.ts` 引用。
 */

'''


def main() -> int:

    rows: list[tuple[str, list[str]]] = []
    for logical in sorted(MODEL_OF_TYPE):
        module, cls_name = MODEL_OF_TYPE[logical]
        importlib.import_module(module)
        cls = getattr(importlib.import_module(module), cls_name, None)
        if cls is None or not hasattr(cls, '__table__'):
            print(f'  ⚠️ 跳过 {logical}：{module}.{cls_name} 不存在')
            continue
        rows.append((logical, [c.name for c in cls.__table__.columns]))

    # 字段中文名：从后端唯一那张表（`patch_schema.FIELD_LABELS`）导出**裸字段名**。
    # 同一个字段名有多个中文名时（`name`/`status`/`title`/`source`）**不进这里** ——
    # 那几个由界面按"字段属于哪类对象"定名（`audit-labels.ts` 的 BY_TYPE）。
    from app.core.patch_schema import FIELD_LABELS

    by_name: dict[str, set[str]] = {}
    for key, label in FIELD_LABELS.items():
        field = key.split('.', 1)[1] if '.' in key else key
        by_name.setdefault(field, set()).add(label)
    ambiguous = sorted(f for f, names in by_name.items() if len(names) > 1)
    safe = {f: next(iter(names)) for f, names in by_name.items() if len(names) == 1}

    lines = [
        HEADER,
        '/** 字段名 → 中文名（只收"含义唯一"的；同名不同义的见 audit-labels.ts 的 BY_TYPE）。 */',
        'export const AUDIT_FIELD_LABEL: Record<string, string> = {',
    ]
    for field in sorted(safe):
        lines.append(f"  {field}: {safe[field]!r},")
    lines += [
        '}',
        '',
        '/** 同名但中文含义随对象而变的字段：**只能**由调用方按对象类型定名。 */',
        'export const AUDIT_AMBIGUOUS_FIELDS: readonly string[] = ['
        + ', '.join(repr(f) for f in ambiguous) + ']',
        '',
        'export const AUDIT_TYPE_FIELDS: Record<string, readonly string[]> = {',
    ]
    for logical, cols in rows:
        lines.append(f"  {logical}: [{', '.join(repr(c) for c in cols)}],")
    lines += ['}', '']

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(lines), encoding='utf-8')
    total = sum(len(cols) for _, cols in rows)
    print(f'✓ 写入 {OUT.relative_to(REPO)}：字段中文名 {len(safe)} 个'
          f'（同名不同义 {len(ambiguous)} 个：{"、".join(ambiguous)}）；'
          f'{len(rows)} 类对象、{total} 个字段')
    return 0


if __name__ == '__main__':
    sys.exit(main())
