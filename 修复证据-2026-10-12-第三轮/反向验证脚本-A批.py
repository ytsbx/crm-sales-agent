"""反向验证脚本（第三轮）：把第 1/3/4/5/6/7 项修复退回旧写法（A 批）。

用法：
    python3 /tmp/revert3_a.py apply
    python3 /tmp/revert3_a.py restore
"""

import io
import sys

ROOT = "/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/backend/"
EDITS: list[tuple[str, str, str]] = []


def edit(path, old, new):
    EDITS.append((path, old, new))


# ---- 第 1 项：类型判定退回"按完整串比较 + 后缀兜底" ----
edit(
    "app/modules/file/router.py",
    """    mime = normalize_mime(record.mime_type)
    suffix = Path(record.file_name or "").suffix.lower()
    if mime in DANGEROUS_MIME_EXACT or suffix in DANGEROUS_SUFFIXES:
        return None
    if mime not in UNKNOWN_MIME:
        if mime.startswith(PREVIEWABLE_MIME_PREFIXES) or mime in PREVIEWABLE_MIME_EXACT:
            return mime
        return None
    return SUFFIX_FALLBACK_TYPES.get(suffix)""",
    """    mime = (record.mime_type or "").lower()
    suffix = Path(record.file_name or "").suffix.lower()
    if mime in DANGEROUS_MIME_EXACT or suffix in DANGEROUS_SUFFIXES:
        return None
    if mime.startswith(PREVIEWABLE_MIME_PREFIXES) or mime in PREVIEWABLE_MIME_EXACT:
        return mime
    if suffix in PREVIEWABLE_SUFFIXES:
        return "application/octet-stream"
    return None""",
)

# ---- 第 3 项：去掉发货日与完成闸门的未来日期校验 ----
edit(
    "app/modules/order/service.py",
    """    ship_date = payload.actual_ship_date or today_business()
    if ship_date > today_business():
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"实际发货日（{ship_date}）不能晚于今天（{today_business()}）："
            "还没发生的日期不是发货事实。要提前排产请填「计划发货日」；"
            "确已发货、只是日期填错了，请按真实发生的那天补录。",
            422,
        )""",
    """    ship_date = payload.actual_ship_date or today_business()""",
)
edit(
    "app/modules/order/service.py",
    """        future = [
            row for row in overview["batches"]
            if row.get("actual_ship_date") is not None
            and row["actual_ship_date"] > today_business()
        ]
        if future:
            detail = "、".join(
                f"第 {row['batch_no']} 批（{row['actual_ship_date']}）" for row in future
            )
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"有发货批次的实发日期在未来（{detail}），不能作为履约凭据，"
                "订单不能标记完成。请先把这些批次的实发日期改回真实发生的那天。",
            )
        if ordered > 0 and shipped < ordered:""",
    """        if ordered > 0 and shipped < ordered:""",
)

# ---- 第 4 项：附件可见性退回"只认客户当前负责人" ----
edit(
    "app/modules/file/access.py",
    """    if business_type == "contract":
        from app.modules.contract.model import ContractDocument
        from app.modules.contract.visibility import contract_in_scope

        doc = await session.get(ContractDocument, business_id)
        if doc is None or doc.deleted_at is not None:
            return False
        return await contract_in_scope(session, user, doc)""",
    """    if business_type == "contract":
        from app.modules.contract.model import ContractDocument

        doc = await session.get(ContractDocument, business_id)
        if doc is None or doc.deleted_at is not None:
            return False
        customer = await session.get(Customer, doc.customer_id)
        if customer is None:
            return False
        owner_ids = await scoped_owner_ids(session, user)
        return owner_ids is None or customer.owner_id in owner_ids""",
)

# ---- 第 5 项：判据退化成空操作（等价于"复制/复购不做可用性检查"）----
edit(
    "app/modules/product/service.py",
    """    problems: list[str] = []
    for sku_id in sorted({int(x) for x in sku_ids if x is not None}):
        try:
            await require_sku_for_new_business(session, sku_id)
        except AppError as error:
            problems.append(error.message)""",
    """    problems: list[str] = []
    return
    for sku_id in sorted({int(x) for x in sku_ids if x is not None}):
        try:
            await require_sku_for_new_business(session, sku_id)
        except AppError as error:
            problems.append(error.message)""",
)

# ---- 第 6 项：提醒扫描不再看"是否已被替代" ----
edit(
    "app/modules/contract/service.py",
    """        if not should_remind(doc):
            continue
        source_key = _monthly_remind_key(doc.id, doc.expiry_date)""",
    """        source_key = _monthly_remind_key(doc.id, doc.expiry_date)""",
)

# ---- 第 7 项：去掉续签生效日的两条校验 ----
edit(
    "app/modules/contract/service.py",
    """        if parent.doc_type == "monthly":
            if payload.effective_date is None:
                raise AppError(
                    ErrorCode.REQUIRED_FIELD_MISSING,
                    "月结协议续签必须填写**生效日**：协议什么时候开始适用，"
                    "决定旧协议还要继续适用到哪一天。请补上生效日再生成。",
                    422,
                )
            if (
                payload.expiry_date is not None
                and payload.effective_date > payload.expiry_date
            ):
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"生效日（{payload.effective_date}）不能晚于到期日"
                    f"（{payload.expiry_date}）—— 一份协议不可能到期之后才生效。",
                    422,
                )""",
    """        pass""",
)
edit(
    "app/modules/contract/service.py",
    """    if (
        payload.effective_date is not None
        and payload.expiry_date is not None
        and payload.effective_date > payload.expiry_date
    ):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"生效日（{payload.effective_date}）不能晚于到期日（{payload.expiry_date}）"
            "—— 一份协议不可能到期之后才生效。",
            422,
        )

    content, filled, missing = render_template(""",
    """    content, filled, missing = render_template(""",
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

    for path, _o, _n in EDITS:
        shutil.copyfile("/tmp/round3_revert_backup/" + path.replace("/", "_"), ROOT + path)
        print(f"  已还原：{path}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    {"apply": apply, "restore": restore}[action]()
