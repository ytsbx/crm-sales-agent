"""老数据导入的「最后联系日期」解析（§六 :167）。

文档要求「导入不应重置客户最近有效联系时间」。可执行的口径是：文件里给了
历史日期就按真实的写；给不出来（空、乱码、未来日期）就留空，落回"刚建档"——
绝不能在解析失败时把"今天"填进去，那才是真的把老客户的重置成刚联系过。
"""

from datetime import UTC, datetime, timedelta

from app.modules.customer.io import TEMPLATE_HEADERS, parse_date


def test_template_exposes_the_column():
    assert "最后联系日期" in TEMPLATE_HEADERS


def test_parses_common_date_formats():
    for raw in ("2025-03-08", "2025/3/8", "2025.03.08", "2025-03-08 10:30"):
        parsed = parse_date(raw)
        assert parsed is not None, raw
        assert (parsed.year, parsed.month, parsed.day) == (2025, 3, 8)
        assert parsed.tzinfo is not None  # 库里是 timezone-aware 列，比较不能混 aware/naive


def test_blank_and_garbage_stay_empty():
    for raw in (None, "", "   ", "去年", "2025-13-45", "N/A"):
        assert parse_date(raw) is None, raw


def test_future_date_is_rejected():
    # 老名单里出现未来日期多半是填错；采信它等于造出一个"永不冷落"的客户
    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
    assert parse_date(future) is None
