"""反向验证脚本：把第 1/4/5/6 项修复退回旧写法（B 批，并发相关）。

用法：
    python3 /tmp/revert_batch_b.py apply
    python3 /tmp/revert_batch_b.py restore
"""

import io
import sys

ROOT = "/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/backend/"
EDITS: list[tuple[str, str, str]] = []


def edit(path, old, new):
    EDITS.append((path, old, new))


# ---- 第 1 项：锁后不再强制重读（退回旧写法：拿身份映射里的旧对象算）----
edit(
    "app/modules/quote/service.py",
    """            select(model)
            .where(model.id == row_id)
            .execution_options(populate_existing=True)
        )""",
    """            select(model).where(model.id == row_id)
        )""",
)
edit(
    "app/modules/quote/service.py",
    """            select(QuoteItem)
            .where(QuoteItem.quote_version_id == version.id)""",
    """            select(QuoteItem).where(QuoteItem.quote_version_id == version.id)""",
)
edit(
    "app/modules/quote/service.py",
    """            select(QuoteCharge)
            .where(QuoteCharge.quote_version_id == version.id)
            .execution_options(populate_existing=True)
        )""",
    """            select(QuoteCharge).where(QuoteCharge.quote_version_id == version.id)
        )""",
)

# ---- 第 4 项：外部结果回来后不再重新加锁刷新 ----
edit(
    "app/modules/erp/service.py",
    """    refreshed = await _lock_order(session, order.id)
    if refreshed is None:
        log.error_message = "拉取结果到达时订单已不存在，未写状态"
        return {
            "status": None,
            "status_label": None,
            "changed": False,
            "blocked_reason": "拉取结果到达时订单已不存在，未写状态",
            "raw_status": result.get("raw_status"),
            "shipped_at": result.get("shipped_at"),
        }
    order = refreshed

    new_status = result.get("status")""",
    """    new_status = result.get("status")""",
)

# ---- 第 5 项：推单不再拦已取消订单 ----
edit(
    "app/modules/erp/service.py",
    """    if order.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"订单 {order.order_no} 已取消，不能再向 {adapter.label} 推送建单。"
            "如果对方系统里确实已经存在这张单，请用「对账/核定」入口登记外部单号，"
            "而不是重新推送。",
            400,
        )

    payload = await build_order_payload(session, order)""",
    """    payload = await build_order_payload(session, order)""",
)

# ---- 第 6 项：幂等指纹退回不含 order_id，并去掉回放前的归属复验 ----
edit(
    "app/modules/payment/router.py",
    """    fingerprint = {**data, "order_id": order_id}
    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="receivable:generate",
            request_key=request_key,
            payload=fingerprint,
            result_type="receivable_plan",
        )
        if reservation.should_replay:
            # 回放前还要**复验那份结果确实是这张订单的**（存在性 + 归属）：
            # 加指纹之前的旧记录、以及"结果 id 指向别的单"这类异常，都不能当成功返回。
            # 数据范围由上面 `assert_order_visible` 对本次 order_id 的校验兜住。
            await _assert_replay_belongs_to_order(
                session, order_id=order_id, plan_id=reservation.replay_id
            )
            return ok(""",
    """    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="receivable:generate",
            request_key=request_key,
            payload=data,
            result_type="receivable_plan",
        )
        if reservation.should_replay:
            return ok(""",
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
        shutil.copyfile("/tmp/round2_revert_backup/" + path.replace("/", "_"), ROOT + path)
        print(f"  已还原：{path}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    {"apply": apply, "restore": restore}[action]()
