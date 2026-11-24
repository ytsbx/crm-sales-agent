"""B15–B19 第二轮（P2）证伪复现。

写法：能静态断言的直接读函数源码；能构造的最小对象直接构造。
断言方向是“缺陷不存在时应当成立”，所以失败=缺陷存在。
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.modules.agent import insights, tools
from app.modules.analytics import service as analytics_service
from app.modules.analytics import target_bases, targets
from app.modules.cases import service as case_service
from app.modules.inquiry import service as inquiry_service
from app.modules.inquiry import router as inquiry_router
from app.modules.integration.model import IntegrationLog
from app.modules.wecom import service as wecom_service


# ---------- B15 企微离职交接 ----------


def test_b15_04_retry_should_treat_null_owner_as_not_belonging():
    """重试搬运时，负责人已被清空（NULL）不能被当成“仍属于离职人”。"""
    src = inspect.getsource(wecom_service._retry_move_crm)
    assert "owner_id is not None and row.owner_id != handover.id" not in src, (
        "NULL 负责人被当成仍归属离职人，会被重新挂给接手人"
    )


def test_b15_05_preview_should_warn_takeover_without_wecom():
    """接管人没绑企微时，预览不能只警告离职人。"""
    src = inspect.getsource(wecom_service.preview_transfer)
    assert "takeover.wecom_userid" in src or "item_assignee.wecom_userid" in src, (
        "预览不校验接管人企微绑定，执行时才逐条失败"
    )


def test_b15_06_integration_log_event_key_should_be_unique():
    """同一事件重放不能写多条 IntegrationLog。"""
    cols = {c.name: c for c in IntegrationLog.__table__.columns}
    assert "event_key" in cols
    assert cols["event_key"].unique is True, "event_key 无唯一约束，重复消费会重复落日志"


# ---------- B16 Agent ----------


def test_b16_02_agent_money_rounder_should_not_apply_to_quantity_weight():
    """_money/round(float,2) 只能用于金额，数量和重量不能被砍到 2 位。"""
    src = inspect.getsource(insights)
    assert 'round(float(value), 2)' not in src or "quantity" not in src, (
        "insights 里用 _f/round(float,2) 统一处理数量/重量，3 位小数会丢"
    )


def test_b16_03_quote_draft_should_validate_customer_matches_opportunity():
    """报价草稿传入的 customer_id 必须与商机客户一致。"""
    src = inspect.getsource(insights.quote_draft)
    assert "opportunity.customer_id" in src and "!=" in src, (
        "quote_draft 未校验 customer_id，能把报价挂到与商机不同的客户"
    )


def test_b16_04_missing_task_permission_should_not_become_zero():
    """没有 task:view 时不能把“未授权”静默压成 0 条。"""
    src = inspect.getsource(insights.followup_suggestion)
    assert "or []" not in src, "权限不足被 `or []` 吞成 0，用户会误以为真的没有任务"


def test_b16_05_delete_conversation_should_clear_agent_executions():
    """删除会话必须同步清理执行记录，读取时也要校验 deleted_at。"""
    from app.modules.agent import router as agent_router

    delete_src = inspect.getsource(agent_router.delete_session)
    assert "delete(AgentExecution)" in delete_src, "删除会话不清执行记录"
    read_src = inspect.getsource(agent_router.get_execution)
    assert "deleted_at" in read_src, "删除后仍可按 id 读执行记录"


def test_b16_06_tool_non_app_error_should_not_be_reported_as_ai_unavailable():
    """工具抛非 AppError 时不能笼统报“AI 服务不可用”，且要落失败记录。"""
    from app.modules.agent import runtime

    src = inspect.getsource(runtime.run_turn)
    idx = src.index("result = await spec.handler(ctx, **args)")
    tool_block = src[idx : idx + 3000]
    assert "except Exception" in tool_block, "工具非 AppError 异常未兜底，会直达 run_turn 被当成模型故障"


# ---------- B17 报表/工具 ----------


def test_b17_02_money_helper_should_not_be_used_for_quantity():
    src = inspect.getsource(tools)
    assert '"quantity": _money(' not in src, "_money 被用于数量，3 位小数被 round 到 2 位"
    assert '"weight": _money(' not in src, "_money 被用于重量，3 位小数被 round 到 2 位"


def test_b17_03_report_accumulation_should_use_decimal():
    """报表多行金额累加/平均不能用 float。"""
    src = inspect.getsource(analytics_service.product_stats)
    assert "Decimal" in src, "product_stats 逐行 float 累加，0.1+0.2 会差分"


def test_b17_04_currency_note_should_probe_quotes_too():
    """报表币种提示只探四张表，报价侧的外币不会触发提示。"""
    src = inspect.getsource(analytics_service.currency_note)
    assert "Quote." in src or "QuoteVersion." in src, (
        "币种探测没有覆盖 Quote，外币报价在报表里不提示"
    )


def test_b17_05_product_bucket_should_keep_spec_for_no_sku():
    """无 SKU 的定制项只按名称归并，丢规格会把不同规格合成一行。"""
    src = inspect.getsource(analytics_service._product_bucket)
    assert "spec" in src, "分组键只有名称，规格不同的定制项被合并"


def test_b17_06_product_stats_should_exclude_private_drafts():
    """产品统计不能把私密草稿报价算进去。"""
    src = inspect.getsource(analytics_service.product_stats)
    assert "draft_scope" in src or "private_draft" in src, "product_stats 未过滤私密草稿"


def test_b17_07_logistics_quantity_should_be_converted_to_decimal():
    """把 float 数量直接传给 Decimal 运算会 TypeError。"""
    from decimal import Decimal as D

    with pytest.raises(TypeError):
        _ = D("1") * 1.5  # 证明 Decimal * float 必炸
    src = inspect.getsource(tools.calculate_logistics)
    assert "Decimal" in src or "str(quantity)" in src, "quantity 以 float 直传 Decimal.prepare"


# ---------- B18 目标/考核 ----------


def test_b18_01_target_bases_should_filter_currency():
    src = inspect.getsource(target_bases.annual_bases)
    assert "currency" in src, "年度实绩跨币种直接相加，无币种过滤/提示"


def test_b18_02_target_snapshot_should_keep_exact_money():
    """审计快照只存 float，无法精确复算到分。"""
    src = inspect.getsource(targets.target_snapshot)
    assert "sales_target_exact" in src, "目标审计快照用 float，改前改后金额只剩二进制近似"


def test_b18_03_frozen_period_missing_metric_should_not_fall_back_to_live():
    """已冻结期间缺某指标键时，返回实时值并仍标 actual_frozen=True。"""
    src = inspect.getsource(targets.targets_with_actuals)
    assert "if key not in snap:\n                    return live" not in src, (
        "冻结期缺键回落实时值，冻结报表仍会漂移"
    )


def test_b18_04_revision_should_be_editable_by_creator():
    """主管代开修订稿后，author_id 仍是原作者，主管改不了。"""
    src = inspect.getsource(case_service.revise_case)
    assert "author_id=user.id" in src or "author_id=current" in src, (
        "修订稿 author_id 固定为原作者，代开稿的主管无法编辑/提交"
    )


# ---------- B19 询价/线索 ----------


def test_b19_01_draft_quote_owner_should_be_persisted_to_chain_column():
    src = inspect.getsource(inquiry_router.create_inquiry)
    assert "oa_quote_user_id=" in src, "草稿里的报价员只进 extra，列上为空，重开需再选"


def test_b19_01b_revision_should_copy_chain_quote_owner():
    src = inspect.getsource(inquiry_router.revise_inquiry)
    assert "oa_quote_user_id=old.oa_quote_user_id" in src, "修订新版丢掉链级报价员"


def test_b19_02_delete_followup_should_refresh_next_followup_at():
    from app.modules.followup import router as followup_router

    src = inspect.getsource(followup_router)
    delete_src = src[src.index("async def delete_followup"):]
    delete_src = delete_src[: delete_src.index("\n@router") if "\n@router" in delete_src else len(delete_src)]
    assert "refresh_next_followup_at" in delete_src, "删除跟进后 next_followup_at 仍指向已删记录"


def test_b19_03_inquiry_serialize_should_keep_exact_money():
    src = inspect.getsource(inquiry_service.serialize)
    assert "target_price_exact" in src, "询价目标价只出 float，前端/审计复算会差分"


def test_b19_04_lead_import_should_not_echo_raw_exception():
    from app.modules.lead import io_router

    src = inspect.getsource(io_router)
    assert "str(exc)[:160]" not in src, "导入单行失败把底层异常原文回显给前端"


def test_b19_05_lock_quote_source_should_apply_draft_scope():
    src = inspect.getsource(inquiry_service.lock_quote_source)
    assert "viewer_id" in src or "draft_scope" in src, (
        "锁报价来源不按查看者过滤私密草稿，会用别人的报价号 409 挡住正常报价"
    )
