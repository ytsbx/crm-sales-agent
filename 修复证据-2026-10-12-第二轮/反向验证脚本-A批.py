"""反向验证脚本：把第 2/3/8/9/10/11 项修复退回旧写法（A 批）。

用法：
    python3 /tmp/revert_batch_a.py apply    # 退回旧写法
    python3 /tmp/revert_batch_a.py restore  # 逐字节还原
"""

import io
import sys

ROOT = "/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/backend/"
EDITS: list[tuple[str, str, str]] = []


def edit(path, old, new):
    EDITS.append((path, old, new))


# ---- 第 2 项：Excel 文件退回"只看主数据" ----
edit(
    "app/modules/bizdoc/service.py",
    """    reasons: list[str] = []
    if version_row.approval_status != "approved":
        reasons.append(
            "报价未通过审批（当前状态："
            + {
                "pending": "待审批",
                "rejected": "已拒绝",
                "draft": "尚未提交审批",
            }.get(version_row.approval_status or "", version_row.approval_status or "未知")
            + "）"
        )
    master_problems = await quote_service.master_confirmation_problems(
        session, version_id=quote_version_id
    )
    if master_problems:
        reasons.append("主数据未确认：" + "；".join(master_problems))
    charges = await quote_service.version_charges(session, quote_version_id)
    freight_reason = quote_service.quote_freight_unconfirmed_reason(charges)
    if freight_reason:
        reasons.append(freight_reason)
    # **只标草稿原因，不动 `allow_draft`** —— 后者会把模板闸门一起关掉
    draft_reason = "；".join(reasons) if reasons else None""",
    """    master_problems = await quote_service.master_confirmation_problems(
        session, version_id=quote_version_id
    )
    draft_reason = None
    if master_problems:
        draft_reason = "主数据未确认：" + "；".join(master_problems)""",
)

# ---- 第 3 项：毛利率退回"缺成本按 0、负收入翻正" ----
edit(
    "app/modules/approval/rules_engine.py",
    """    any_missing_cost = any(cost <= 0 for cost in unit_costs)
    if any_missing_cost or revenue_for_margin <= 0:
        gross_margin = None
    else:
        gross_margin = float((revenue_for_margin - total_cost) / revenue_for_margin * 100)""",
    """    gross_margin = (
        float((revenue_for_margin - total_cost) / revenue_for_margin * 100)
        if revenue_for_margin
        else 0.0
    )""",
)
edit(
    "app/modules/approval/rules_engine.py",
    """    if any_missing_cost or any(p <= 0 for p in prices) or not items:
        min_item_margin = None
    else:
        min_item_margin = min(
            float((p - c) / p * 100) for p, c in zip(prices, unit_costs)
        )""",
    """    item_margins = [
        float((p - c) / p * 100) for p, c in zip(prices, unit_costs) if p
    ]
    min_item_margin = min(item_margins) if item_margins else 0.0""",
)
edit(
    "app/modules/approval/rules_engine.py",
    """        "gross_margin": round(gross_margin, 2) if gross_margin is not None else None,
        "min_item_margin": (
            round(min_item_margin, 2) if min_item_margin is not None else None
        ),""",
    """        "gross_margin": round(gross_margin, 2),
        "min_item_margin": round(min_item_margin, 2),""",
)

# ---- 第 8 项：单价退回两位小数 ----
edit(
    "app/modules/bizdoc/xlsx.py",
    """    if key == "amount":
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return _text(value)
    if key == "unit_price":
        return format_unit_price(value)
    return _text(value)""",
    """    if key in ("unit_price", "amount"):
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return _text(value)
    return _text(value)""",
)
edit(
    "app/modules/quote/pdf.py",
    """                format_unit_price(item.get("quoted_price", 0)),""",
    """                f"{item.get('quoted_price', 0):.2f}",""",
)

# ---- 第 9 项：不再强制字符串类型 ----
edit(
    "app/modules/bizdoc/xlsx.py",
    """        cell = sheet.cell(row=row, column=column, value=text)
    cell.data_type = "s"
    return cell""",
    """        cell = sheet.cell(row=row, column=column, value=text)
    return cell""",
)
edit(
    "app/modules/bizdoc/xlsx.py",
    """            if key not in ("unit_price", "amount"):
                cell.data_type = "s"
            cell.border = BORDER""",
    """            cell.border = BORDER""",
)

# ---- 第 10 项：审批档位退回原币金额、去掉缺汇率拦截 ----
edit(
    "app/modules/quote/service.py",
    """    currency_code = (version.currency or "CNY").upper()
    if currency_code != "CNY" and not (fx and fx > 0):
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            f"该版本币种是 {currency_code}，但没有汇率快照，无法折算人民币金额来确定审批档位。"
            "请先在版本上补汇率（或新建版本重新取汇率），再提交审批。",
            422,
        )""",
    """    currency_code = (version.currency or "CNY").upper()  # 退回：不拦缺汇率""",
)
edit(
    "app/modules/quote/service.py",
    """    levels = await settings_service.get_list(session, "approval_levels")
    amount = total_amount_cny""",
    """    levels = await settings_service.get_list(session, "approval_levels")
    amount = float(version.total_amount or 0)""",
)
edit(
    "app/modules/quote/service.py",
    """    total_amount_cny = (
        float((version.total_amount or 0) * fx)
        if currency_code != "CNY"
        else float(version.total_amount or 0)
    )""",
    """    total_amount_cny = (
        float((version.total_amount or 0) * fx)
        if currency_code != "CNY" and fx
        else float(version.total_amount or 0)
    )""",
)

# ---- 第 11 项：汇率快照退回"重算之后才落" ----
edit(
    "app/modules/quote/service.py",
    """    rate_warning = await apply_exchange_rate_snapshot(
        session, version, currency=source.currency, explicit_rate=None
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)

    # 复制过来的明细快照是**按源版本口径**算的，而新版本已经切到当前口径""",
    """    # 复制过来的明细快照是**按源版本口径**算的，而新版本已经切到当前口径""",
)
edit(
    "app/modules/quote/service.py",
    """    await close_superseded_approvals(session, quote=quote, new_version=version, user=user)
    return version""",
    """    await close_superseded_approvals(session, quote=quote, new_version=version, user=user)

    rate_warning = await apply_exchange_rate_snapshot(
        session, version, currency=source.currency, explicit_rate=None
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)
    return version""",
)


def apply():
    for path, old, new in EDITS:
        full = ROOT + path
        text = io.open(full, encoding="utf-8").read()
        if old not in text:
            raise SystemExit(f"退回失败（没找到目标片段）：{path}")
        io.open(full, "w", encoding="utf-8").write(text.replace(old, new, 1))
        print(f"  已退回：{path}")


def restore():
    import shutil

    for path, _old, _new in EDITS:
        src = "/tmp/round2_revert_backup/" + path.replace("/", "_")
        shutil.copyfile(src, ROOT + path)
        print(f"  已还原：{path}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if action == "apply":
        apply()
    elif action == "restore":
        restore()
    else:
        raise SystemExit("用法：revert_batch_a.py apply|restore")
