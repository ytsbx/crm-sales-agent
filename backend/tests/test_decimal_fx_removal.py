"""外币死行拆除回归锁（主人 2026-11-24 确认：本部署无外币老单、也不会存在外币）。

拆除范围：quote/pricing/rules_engine 里"是外币才走"的折算半截与 fx 参数链；
保留：CNY 直通路径、exchange_rate 字段形状（恒 None）、汇率表模型、
入口 trade_mode 开关、国内非零退税率拒绝守卫。
非 CNY 一律显式拒绝（项目规矩：非人民币明确拒绝），不再静默算人民币数字贴外币标签。
"""
from decimal import Decimal as D

import pytest

from app.core.errors import AppError
from app.modules.pricing.service import calculate_price
from app.modules.quote.service import convert_cny_to


def test_convert_cny_to_cny_passthrough():
    assert convert_cny_to(D("12.34"), to_currency="CNY") == (D("12.34"), None)
    assert convert_cny_to(None, to_currency="CNY") == (None, None)


def test_convert_cny_to_foreign_explicit_reject():
    with pytest.raises(AppError) as ei:
        convert_cny_to(D("12.34"), to_currency="USD")
    assert "人民币" in str(ei.value)


@pytest.mark.anyio
async def test_calculate_price_foreign_explicit_reject(db_session):
    # 守卫在一切 DB 调用之前抛错，同步 Session 替身即可
    with pytest.raises(AppError) as ei:
        await calculate_price(db_session, sku_id=1, quantity=D(1), currency="USD")
    assert "人民币" in str(ei.value)