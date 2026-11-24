"""
B16–B20 高严重度复核复现（证伪用）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b16_b20_highlights.py -q

本批只有 B16-01、B17-01 两条 P1；B18–B20 报告自身 0 P1。
"""
from __future__ import annotations

import inspect


# ---------------------------------------------------------------- B16-01
def test_b16_01_redispatch_audit_should_redact_wecom_error():
    """补投把 wecom_error 原样写审计并回显；上游可能是带 access_token 的 httpx 异常。"""
    from app.modules.notification import router as notification_router

    source = inspect.getsource(notification_router.redispatch_notification)
    assert '"wecom_error": row.wecom_error' not in source, (
        "补投审计仍原样落 wecom_error；B15-03 的 access_token 会从通知字段扩散到长期审计"
    )
    assert '"wecom_error": row.wecom_error,' not in source, "响应仍原样回显 wecom_error"


def test_b16_01_response_should_not_echo_raw_error():
    from app.modules.notification import router as notification_router

    source = inspect.getsource(notification_router.redispatch_notification)
    assert "f\"仍未能投出：{row.wecom_error" not in source, (
        "失败提示仍直接拼 row.wecom_error，未脱敏"
    )


# ---------------------------------------------------------------- B17-01
def test_b17_01_pending_receivable_should_not_use_empty_orders():
    """无 order:view 时 orders=[]，待回款 = 0 − 已回款 → 负数。"""
    from app.modules.agent import tools

    source = inspect.getsource(tools.get_customer_overview)
    assert (
        "if not ctx.user.has(\"order:view\")" in source
        or "order_total" not in source.split("if ctx.user.has(\"payment:view\")")[1]
    ), (
        "payment:view 分支仍遍历可能为空的 orders 算 order_total；"
        "无 order:view 时待回款会变成负数"
    )
