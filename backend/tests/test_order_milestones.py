"""跟单里程碑（order/milestones.py）纯函数测试：倒推计划与状态判定。"""

from datetime import date

from app.modules.order.milestones import (
    MILESTONE_NODES,
    STATUS_DONE,
    STATUS_OVERDUE,
    STATUS_PENDING,
    default_plan,
    node_status,
)

DELIVERY = date(2026, 10, 20)


class TestDefaultPlan:
    def test_six_leader_nodes(self):
        assert [key for key, _label, _offset in MILESTONE_NODES] == [
            "contract",
            "deposit",
            "pre_sample_sent",
            "pre_sample_confirmed",
            "first_shipment",
            "payment",
        ]

    def test_backward_from_delivery(self):
        plan = default_plan(DELIVERY)
        # 首批发货 = 交期当天
        assert plan["first_shipment"] == DELIVERY
        # 产前样确认 = 交期前 15 天
        assert plan["pre_sample_confirmed"] == date(2026, 10, 5)
        # 产前样发出早于产前样确认，签订合同最早
        assert plan["pre_sample_sent"] < plan["pre_sample_confirmed"]
        assert plan["contract"] < plan["deposit"] < plan["pre_sample_sent"]
        # 收款在交期之后（账期）
        assert plan["payment"] > DELIVERY

    def test_unknown_delivery_gives_empty_plan(self):
        plan = default_plan(None)
        assert all(v is None for v in plan.values())


class TestNodeStatus:
    def test_actual_set_is_done_even_if_late(self):
        assert node_status(date(2026, 10, 1), date(2026, 10, 9), date(2026, 10, 10)) == STATUS_DONE

    def test_past_planned_without_actual_is_overdue(self):
        assert node_status(date(2026, 10, 1), None, date(2026, 10, 10)) == STATUS_OVERDUE

    def test_planned_today_is_pending(self):
        assert node_status(date(2026, 10, 10), None, date(2026, 10, 10)) == STATUS_PENDING

    def test_future_is_pending(self):
        assert node_status(date(2026, 10, 20), None, date(2026, 10, 10)) == STATUS_PENDING

    def test_no_planned_no_actual_is_pending(self):
        assert node_status(None, None, date(2026, 10, 10)) == STATUS_PENDING
