"""案例分享版脱敏（§3.7/场景15）。

文档原话：「案例分享版对客户**电话、合同、成本、特殊价**等做权限控制或脱敏；
原单据仍按业务权限访问。」这里锁住四类都脱、做法文本不被误伤、
以及"作者看原文但能看到脱敏命中"这两条口径。
"""

from app.modules.cases.model import SalesCase
from app.modules.cases.redaction import evidence_scope_for, mask_text
from app.modules.cases.service import serialize_case


class _User:
    """够 serialize_case / evidence_scope_for 用的最小用户替身。"""

    def __init__(self, permissions=(), roles=()):
        self.permissions = set(permissions)
        self.roles = list(roles)

    def has(self, code: str) -> bool:
        return code in self.permissions


def _case(**overrides) -> SalesCase:
    defaults = dict(
        id=1,
        title="把价格异议谈成首单",
        author_id=7,
        customer_id=99,
        customer_label="华东某代工厂",
        lessons="先算客户的单件成本，再谈总价",
    )
    defaults.update(overrides)
    return SalesCase(**defaults)


def test_masks_phone_amount_discount_and_contract():
    text = "客户嫌 8.5 元太贵（电话 13812345678），成本 5 元，报了 95 折，合同 HT2024001"
    masked, counters = mask_text(text)
    assert "13812345678" not in masked
    assert "8.5 元" not in masked
    assert "95 折" not in masked
    assert "HT2024001" not in masked
    assert counters["手机号"] == 1
    assert counters["折扣"] == 1
    assert counters["合同编号"] == 1
    # 金额类可能命中 8.5 元 与 5 元 两处，口径是"抹干净"，具体条数不锁死
    assert counters["金额"] >= 2


def test_keeps_practice_text_untouched():
    text = "先问清用途，再寄产前样；客户确认后签合同，注意先收定金"
    masked, counters = mask_text(text)
    assert masked == text
    assert counters == {}


def test_share_view_masks_text_and_hides_identity_and_evidence():
    case = _case(
        process="报价 12.5 元，客户要 30 天账期，手机 13900001111",
        quote_id=31,
        order_id=44,
    )
    payload = serialize_case(
        case,
        customer_name="某某精密制造有限公司",
        reveal_customer=False,
        evidence_scope={"quote_id"},  # 只有报价查看权限
    )
    assert payload["share_view"] is True
    assert payload["customer_id"] is None
    assert payload["customer_name"] is None
    assert payload["customer_label"] == "华东某代工厂"
    assert "13900001111" not in payload["process"]
    assert "12.5 元" not in payload["process"]
    # 有权看报价就留报价引用，没权看订单就不给订单 id
    assert payload["quote_id"] == 31
    assert payload["order_id"] is None
    assert payload["hidden_evidence"] == ["order_id"]
    assert payload["redaction_summary"]  # 抹了什么要说得清


def test_author_view_keeps_text_but_reports_hits():
    case = _case(process="成本 5 元，报价 8.5 元")
    payload = serialize_case(case, reveal_customer=True, evidence_scope=None)
    assert payload["share_view"] is False
    assert "8.5 元" in payload["process"]  # 作者看原文
    assert payload["redaction_summary"]  # 但要能自查"分享出去会被抹掉什么"


def test_evidence_scope_follows_module_permissions():
    reader = _User(permissions=["quote:view", "order:view"])
    scope = evidence_scope_for(reader)
    assert "quote_id" in scope and "order_id" in scope
    assert "sample_id" not in scope  # 没有样品查看权就不下发打样单 id

    admin = _User(roles=["admin"])
    assert evidence_scope_for(admin) == {
        "quote_id",
        "order_id",
        "sample_id",
        "opportunity_id",
    }
