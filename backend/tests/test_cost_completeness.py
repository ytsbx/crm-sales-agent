"""第七批 P1-1：不完整的成本不能被当成完整成本去算毛利、更不能用它免审。

## 这一条为什么危险

`ProductCost` 的四项成本（采购/生产/包装/加工）都是可空列，`total_cost`
把 NULL 当 0 参与合计 —— 它自己的文档就要求"调用方必须自己看 `is_complete`"。

审查实测的场景：**只填采购成本 10、其余三项为空**，报价 100、保护价 120。
合计成本 10 → 毛利率 90% → 命中"毛利 ≥ 30% 免审" → **低于保护价的报价被自动批准**。

"算不出来"却算出了一个好看的数字 —— 与当初"缺成本按 0 算成 100% 毛利"
是同一类错误，只是这次成本行存在、所以谁都没觉得它缺。

## 这里钉住的判据

1. `item_cost_is_complete`：快照标记 False → 不完整；True → 完整；
   **NULL → 回退到老的"成本 ≤ 0 才算缺失"**（历史明细，刻意不动老行为）。
2. 本列上线后新生成的报价快照，必须**真的落上**这个标记（不是只在核价结果里算一下）。
3. 核价结果里"由成本反推的价格"在不完整时一律不给（保护价不掺成本，但授权底价、
   绝对底价、按成本加目标利润的试算价都是拿成本推出来的）。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from conftest import make_engine
from app.modules.pricing.model import ProductCost
from app.modules.quote.model import QuoteItem
from app.modules.quote.service import item_cost_is_complete


def _item(**kwargs) -> QuoteItem:
    """只造内存对象 —— 本组判据是纯函数，不碰库。"""
    base = dict(
        quote_version_id=1,
        sku_id=1,
        quantity=Decimal(1),
        cost_snapshot=Decimal("10"),
        quoted_price=Decimal("100"),
    )
    base.update(kwargs)
    return QuoteItem(**base)


class TestItemCostIsComplete:
    def test_null_flag_falls_back_to_old_rule(self):
        """历史明细（标记 NULL）：照旧按"成本≤0 才算缺失"，不能一律当成不完整。"""
        assert item_cost_is_complete(_item(cost_complete_snapshot=None, cost_snapshot=Decimal("10")))
        assert not item_cost_is_complete(_item(cost_complete_snapshot=None, cost_snapshot=Decimal("0")))

    def test_explicit_false_is_incomplete(self):
        """★审查那条：成本行在、金额是正数，但四项没填全 → 不完整。"""
        assert not item_cost_is_complete(
            _item(cost_complete_snapshot=False, cost_snapshot=Decimal("10"))
        )

    def test_explicit_true_is_complete(self):
        assert item_cost_is_complete(
            _item(cost_complete_snapshot=True, cost_snapshot=Decimal("10"))
        )

    def test_product_cost_reports_missing_components(self):
        """`is_complete` 的判据本身（只填采购 = 不完整；显式 0 算填了）。"""
        partial = ProductCost(
            sku_id=1,
            effective_from=date(2026, 1, 1),
            purchase_cost=Decimal("10"),
            production_cost=None,
            package_cost=None,
            processing_cost=None,
        )
        assert not partial.is_complete
        assert partial.missing_components == ["生产成本", "包装成本", "加工成本"]
        assert partial.total_cost == Decimal("10")  # 把 NULL 当 0 —— 所以必须另看 is_complete

        full = ProductCost(
            sku_id=1,
            effective_from=date(2026, 1, 1),
            purchase_cost=Decimal("10"),
            production_cost=Decimal("0"),
            package_cost=Decimal("0"),
            processing_cost=Decimal("0"),
        )
        assert full.is_complete, "显式填 0 意思是「不花钱」，不是「没填」，不能当成不完整"


class TestSnapshotPersistsCompleteness:
    """标记要真的落进快照 —— 只在核价结果里算一下是没用的：
    审批读的是 `quote_items` 的快照，不是核价返回值。"""

    def test_new_column_round_trips(self):
        engine = make_engine()
        QuoteItem.__table__.create(engine, checkfirst=True)
        with Session(engine) as session:
            session.add(_item(cost_complete_snapshot=False))
            session.add(_item(cost_complete_snapshot=True))
            session.add(_item(cost_complete_snapshot=None))
            session.commit()
            flags = [
                row.cost_complete_snapshot
                for row in session.query(QuoteItem).order_by(QuoteItem.id).all()
            ]
        assert flags == [False, True, None], (
            "三态必须都能存回来：False=确知不完整、True=确知完整、None=历史未知。"
            "若把 None 也落成 False，历史上所有老报价的毛利免审会全部作废。"
        )
