"""Q7 决策3 边界回归：毛利率判断用真值、展示两位半偶、留痕/脱敏接住精确键。

主人拍板"按3做"：比率是派生指标不是结算钱，舍入后再判等于给判决引入半格误差。
覆盖：边界带 22.048 显示到线但真值未达→不命中；平局 22.045 不命中；22.05 命中；
展示半偶只影响长相；命中明细解释文本与判案依据一致；无权限者精确键一并抹掉。
"""
from decimal import Decimal as D

from app.modules.approval import rules_engine as re_


def _ctx(exact, display=None):
    return {
        "gross_margin": display if display is not None else re_._margin_display(exact),
        "_gross_margin_exact": exact,
    }


def test_display_half_even_is_cosmetic_only():
    assert re_._margin_display(D("22.045")) == 22.04
    assert re_._margin_display(D("22.055")) == 22.06
    assert re_._margin_display(None) is None


def test_boundary_band_display_reaches_line_but_truth_does_not():
    cond = [{"field": "gross_margin", "op": "gte", "value": "22.05"}]
    hit, detail = re_.evaluate_conditions(cond, _ctx(D("22.048")))
    assert hit is False  # 真值 22.048 < 22.05，取消"差几毫米送过杆"
    assert detail[0]["actual"] == D("22.048")
    assert _ctx(D("22.048"))["gross_margin"] == 22.05  # 屏幕仍显示到线（长相）


def test_tie_and_exact_line_judged_on_truth():
    cond = [{"field": "gross_margin", "op": "gte", "value": "22.05"}]
    assert re_.evaluate_conditions(cond, _ctx(D("22.045")))[0] is False
    assert re_.evaluate_conditions(cond, _ctx(D("22.05")))[0] is True
    assert re_.evaluate_conditions(cond, _ctx(D("22.0449")))[0] is False


def test_fields_without_exact_key_keep_old_semantics():
    cond = [{"field": "gross_margin", "op": "gte", "value": "22.05"}]
    assert re_.evaluate_conditions(cond, {"gross_margin": 22.05})[0] is True
    cond_amt = [{"field": "total_amount", "op": "lte", "value": "100000"}]
    assert re_.evaluate_conditions(cond_amt, {"total_amount": D("100000")})[0] is True


def test_min_item_margin_uses_exact_key_too():
    cond = [{"field": "min_item_margin", "op": "gte", "value": "22.05"}]
    ctx = {"min_item_margin": 22.05, "_min_item_margin_exact": D("22.048")}
    assert re_.evaluate_conditions(cond, ctx)[0] is False


def test_explanation_text_matches_judgment_basis():
    cond = [{"field": "gross_margin", "op": "gte", "value": "22.05"}]
    _, detail = re_.evaluate_conditions(cond, _ctx(D("22.048")))
    pretty = re_._cond_detail_pretty(detail)
    assert pretty[0]["actual_label"] == "22.048%"
    _, detail2 = re_.evaluate_conditions(cond, _ctx(D("22.05")))
    assert re_._cond_detail_pretty(detail2)[0]["actual_label"] == "22.05%"


def test_margin_exact_formatter_truthful_digits():
    assert re_._fmt_margin_exact(D("22.048")) == "22.048"
    assert re_._fmt_margin_exact(D("22.5")) == "22.50"
    assert re_._fmt_margin_exact(D("1234.5")) == "1,234.50"
    assert re_._fmt_margin_exact(D("-100")) == "-100.00"


def test_cost_permission_strips_exact_key_too():
    ctx = _ctx(D("22.048"))
    stripped = re_.strip_cost_context(ctx, can_see_cost=False)
    assert stripped["_gross_margin_exact"] == re_.COST_HIDDEN
    assert stripped["gross_margin"] == re_.COST_HIDDEN
    assert re_.strip_cost_context(ctx, can_see_cost=True)["_gross_margin_exact"] == D("22.048")