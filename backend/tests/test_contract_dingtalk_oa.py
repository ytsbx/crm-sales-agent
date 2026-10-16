"""客户起草合同发起钉钉签署：重提口径与"这次到底发生了什么"的如实回报。

守两条 2026-10-16 修的缺陷（都属"加一条单测就会红"的类型）：

1. **驳回/撤回后发不出去**：提交接口原先不传 `resubmit`，而
   `create_inquiry_instance` 对 `rejected`/`withdrawn` 是**原样返回旧行、一个
   请求都不发**。于是撤回一次这份合同就永久发不出去（只能作废重建）。
2. **谎报成功**：无论返回什么状态，界面都弹绿色的"已发起钉钉合同签署申请"，
   而 `rejected`/`withdrawn`/`approved`/`needs_review` 这几种情况下钉钉那边
   什么都没有。

这里只测**纯函数**（不连库）：重提判据与结果口径都必须与共享状态机
（`dingtalk/model.py` 的 `OA_STATUS_ACTIONS`）保持一致，不能各写一套。
"""
from types import SimpleNamespace as Obj

import pytest

from app.modules.contract import router as routes
from app.modules.dingtalk.model import allowed_actions


def _row(status, *, resolve_state="idle", instance_id=None, error=None):
    return Obj(
        status=status,
        resolve_state=resolve_state,
        instance_id=instance_id,
        error=error,
        resolve_request_key=None,
    )


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
def test_terminal_states_are_resubmitted_as_a_new_round(status):
    """驳回 / 已撤回：钉钉那边明确没有活单，必须换一轮重提，否则永远发不出去。"""
    assert routes._contract_oa_resubmit(_row(status)) is True
    # 判据必须来自共享状态机，而不是这里另写一份 if
    assert "resubmit" in allowed_actions(status, "idle")


@pytest.mark.parametrize("status", ["pending", "approved", "needs_review", "submitting", "failed"])
def test_live_or_unknown_rounds_are_never_resubmitted(status):
    """审批中 / 已通过 / 结果未知：钉钉那边可能正有一张活单，**不许**另建一张。"""
    assert routes._contract_oa_resubmit(_row(status)) is False


def test_no_history_means_a_fresh_send_not_a_resubmit():
    assert routes._contract_oa_resubmit(None) is False


def test_resolve_in_progress_blocks_resubmit():
    """占用中（有人正在对这条做外部动作）时连重提都不许，与状态机同一口径。"""
    assert routes._contract_oa_resubmit(_row("rejected", resolve_state="processing")) is False


def test_only_a_real_send_is_reported_as_success():
    """唯一算"发出去了"的情况：pending 且有实例号。"""
    result = routes._contract_oa_result(_row("pending", instance_id="INST-1"), fresh=True)
    assert result["level"] == "success"
    assert result["message"] == "已发起钉钉合同签署申请"

    result = routes._contract_oa_result(_row("pending", instance_id="INST-2"), fresh=False)
    assert result["level"] == "success"
    assert result["message"] == "已重新发起钉钉合同签署申请"


def test_pending_without_instance_is_not_success():
    """钉钉没给出实例号就不算发起成功——这正是原先会谎报的那一类。"""
    result = routes._contract_oa_result(_row("pending", instance_id=None), fresh=True)
    assert result["level"] != "success"


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
def test_ended_rounds_are_reported_honestly(status):
    result = routes._contract_oa_result(_row(status), fresh=False)
    assert result["level"] == "warning"
    assert "没有重新发起" in result["message"]


def test_approved_round_is_not_reported_as_a_new_send():
    result = routes._contract_oa_result(_row("approved", instance_id="INST-1"), fresh=False)
    assert result["level"] == "warning"
    assert "已经通过" in result["message"]


def test_needs_review_tells_the_user_to_check_dingtalk_first():
    """结果未知时**绝不能**报成功，也不能引导用户再点一次。"""
    result = routes._contract_oa_result(_row("needs_review"), fresh=False)
    assert result["level"] == "error"
    assert "不能自动重发" in result["message"]


def test_failure_keeps_the_real_reason():
    result = routes._contract_oa_result(
        _row("failed", error="钉钉明确拒绝了这次发起，没有建单，可以修正后重试｜DingTalkError: x"),
        fresh=True,
    )
    assert result["level"] == "error"
    assert "钉钉明确拒绝" in result["message"]


def test_skipped_gate_off_is_not_reported_as_success():
    """推送总闸关着时是"没发"，不是"发失败"，但**也不是成功**。"""
    result = routes._contract_oa_result(
        _row("skipped", error="钉钉外部调用已关闭（DINGTALK_PUSH_OFF）；未发起审批"),
        fresh=True,
    )
    assert result["level"] == "error"
    assert "DINGTALK_PUSH_OFF" in result["message"]
