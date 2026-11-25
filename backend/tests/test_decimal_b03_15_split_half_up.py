"""B03-15 决策1 边界回归：分期舍入统一四舍五入（与 core/money 同口径）。

主人 2026-11-24 拍板"统一四舍五入"。覆盖：平局点半分向上（0.05 分两期 0.03/0.02）、
多期平局面额、总额守恒锁一分不差、负数期拒绝不变、比例精确 Decimal 入参
（16 位字符串逐位、NaN/Infinity 显式拒绝）。
"""
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.core.money import line_amount
from app.modules.payment.schema import ReceivableGenerate
from app.modules.payment.service import split_amounts


def test_half_cent_tie_rounds_up_like_core_money():
    # 0.025 元平局：四舍五入 0.03，末期补差 0.02（原半偶给 0.02/0.03）
    assert split_amounts(D("0.05"), [D("0.5"), D("0.5")]) == [D("0.03"), D("0.02")]
    # 与 core/money 结算落分同口径：0.025 落分 0.03
    assert line_amount(D("1"), D("0.025")) == D("0.03")


def test_multi_period_tie_faces_and_conservation():
    ratios = [D("0.33345"), D("0.33345"), D("0.3331")]
    amounts = split_amounts(D("100"), ratios)
    assert amounts == [D("33.35"), D("33.35"), D("33.30")]
    assert sum(amounts) == D("100")  # 守恒锁：总额一分不差


def test_conservation_holds_for_arbitrary_ratios():
    for total in ("0.03", "100", "99999999999999.99"):
        amounts = split_amounts(D(total), [D("0.5"), D("0.5")])
        assert sum(amounts) == D(total)


def test_negative_period_still_rejected():
    with pytest.raises(Exception, match="不大于 0"):
        split_amounts(D("0.03"), [D("0.2")] * 5)


def test_ratios_exact_decimal_and_nan_blocked():
    from datetime import date
    ok = ReceivableGenerate(
        ratios=["0.33345", "0.33345", "0.3331"],
        first_due_date=date(2026, 1, 1),
        due_dates=[date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)],
    )
    assert ok.ratios[0] == D("0.33345")
    with pytest.raises(ValidationError, match="finite number|有限数"):
        ReceivableGenerate(ratios=[float("nan"), D("1")], first_due_date=date(2026, 1, 1))
    with pytest.raises(ValidationError, match="finite number|有限数"):
        ReceivableGenerate(ratios=[float("inf"), D("0")], first_due_date=date(2026, 1, 1))