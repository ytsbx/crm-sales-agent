"""float→Decimal 专项 · 批1 回归：JSON 出口与审计留痕一位不差。

钉死三件事（任何一条回退都会红）：
1. Decimal 注册进 ENCODERS_BY_TYPE 后原样通过 jsonable_encoder，不被转 float；
2. 真实 ASGI 链路（FastAPI 默认编码 → DecimalJSONResponse）线上文本是精确数字
   字面量，99999999999999.99 / 四位单价 / 三位数量一位不丢，契约仍是 JSON 数字；
3. 审计 json_safe 把 Decimal 按原值存字符串（B12-01），JSONB 留痕可精确复算。

不删既有断言、不放容差：普通金额仍按数字断言，边界金额按精确值断言。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder

from app.core import jsonio
from app.core.audit import AuditLog, json_safe, write_audit
from app.core.response import ok
from conftest import SyncSessionAsAsync

BIG = Decimal("99999999999999.99")  # Numeric(16,2) 合法上限边界
PRICE = Decimal("999999999999.9999")  # Numeric(16,4) 四位单价边界
QTY = Decimal("9999999999999.988")  # Numeric(16,3) 三位数量边界


def test_decimal_survives_jsonable_encoder():
    """注册点生效：Decimal 过 FastAPI 默认编码后仍是 Decimal 原值。"""
    encoded = jsonable_encoder({"amount": BIG, "nested": [PRICE, QTY]})
    assert isinstance(encoded["amount"], Decimal)
    assert encoded["amount"] == BIG
    assert encoded["nested"] == [PRICE, QTY]


@pytest.mark.anyio
async def test_real_asgi_wire_exact_numbers():
    """真实 FastAPI 链路：线上文本是精确数字字面量，不是 float 近似值。"""
    app = FastAPI(default_response_class=jsonio.DecimalJSONResponse)

    @app.get("/probe")
    async def probe():
        return ok(
            {
                "total_amount": BIG,
                "unit_price": PRICE,
                "quantity": QTY,
                "plain_float": 195.0,
                "count": 3,
                "nothing": None,
            }
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/probe")
    assert resp.status_code == 200
    # 线路文本逐位精确（紧凑分隔符，与 JSONResponse 默认一致）。
    assert '"total_amount":99999999999999.99' in resp.text
    assert '"unit_price":999999999999.9999' in resp.text
    assert '"quantity":9999999999999.988' in resp.text
    # 契约仍是 JSON 数字：按 Decimal 解析回来一位不差；普通 float/int 行为不变。
    parsed = json.loads(resp.text, parse_float=Decimal)
    data = parsed["data"]
    assert data["total_amount"] == BIG
    assert data["unit_price"] == PRICE
    assert data["quantity"] == QTY
    assert data["plain_float"] == Decimal("195.0")
    assert data["count"] == 3 and isinstance(data["count"], int)
    assert data["nothing"] is None


def test_renderer_boundaries_and_escaping():
    """零/负数/科学计数输入/嵌套/字符串转义：渲染精确且可往返。"""
    content = {
        "zero": Decimal("0.00"),
        "neg": Decimal("-1234.56"),
        "sci": Decimal("1E+2"),
        "tiny": Decimal("0.0001"),
        "text": '引号"反斜杠\\换行\n中文\t制表',
        "list": [Decimal("0.01"), {"inner": Decimal("-0.001")}],
    }
    text = jsonio.exact_json_dumps(content)
    assert '"zero":0.00' in text
    assert '"neg":-1234.56' in text
    assert '"sci":100' in text  # 十进制展开，不走科学计数
    assert '"tiny":0.0001' in text
    parsed = json.loads(text, parse_float=Decimal)
    assert parsed["text"] == content["text"]
    assert parsed["list"][0] == Decimal("0.01")
    assert parsed["list"][1]["inner"] == Decimal("-0.001")


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_non_finite_rejected(bad):
    """NaN/Infinity 不许上线（对齐 allow_nan=False），显式报错而不是写坏 JSON。"""
    with pytest.raises(ValueError):
        jsonio.exact_json_dumps({"amount": bad})


def test_json_safe_decimal_to_exact_string():
    """审计留痕：Decimal→原值字符串；datetime/float/容器行为不变。"""
    assert json_safe(BIG) == "99999999999999.99"
    assert json_safe({"a": [BIG, Decimal("0.01")]}) == {
        "a": ["99999999999999.99", "0.01"]
    }
    stamp = datetime(2026, 11, 24, 8, 0, tzinfo=UTC)
    assert json_safe(stamp) == stamp.isoformat()
    assert json_safe(195.0) == 195.0  # 非 Decimal 数字原样（历史形状不变）
    assert json_safe(None) is None


@pytest.mark.anyio
async def test_idempotency_replay_keeps_numeric_shape(db_session):
    """回放契约「同键返回同一份响应」：金额保持数字形状，不随审计改字符串。

    审计字符串化（decimal_as_str 默认 True）与回放数字形状（False）是分开的
    两个契约；这条用例钉死后者，防止以后顺手把回放也改成字符串。
    """
    from app.core import idempotency

    session = SyncSessionAsAsync(db_session)
    reservation = await idempotency.reserve(
        session, user_id=1, action="probe:shape", request_key="k-shape", payload={"a": 1}
    )
    await idempotency.complete(
        session, reservation, result_payload={"amount": Decimal("12.50")}, result_id=9
    )
    # Q4 决策2：快照存精确原文（顶层字符串），回放解包后仍是精确数字形状。
    stored = reservation.row.result_payload
    assert isinstance(stored, str)
    replay = await idempotency.reserve(
        session, user_id=1, action="probe:shape", request_key="k-shape", payload={"a": 1}
    )
    assert replay.should_replay is True
    assert replay.replay_payload["amount"] == Decimal("12.50")


@pytest.mark.anyio
async def test_write_audit_stores_exact_amount_string(db_session):
    """真实 write_audit + JSONB 列：大额边界留痕是精确字符串，可复算。"""
    AuditLog.__table__.create(db_session.get_bind(), checkfirst=True)
    session = SyncSessionAsAsync(db_session)
    await write_audit(
        session,
        operator_id=101,
        action="probe_precision",
        business_type="quote",
        business_id=1,
        before={"total_amount": BIG},
        after={"total_amount": Decimal("0.01"), "unit_price": PRICE},
    )
    await session.flush()
    row = db_session.query(AuditLog).filter_by(action="probe_precision").one()
    assert row.before_data["total_amount"] == "99999999999999.99"
    assert row.after_data["total_amount"] == "0.01"
    assert row.after_data["unit_price"] == "999999999999.9999"
    # 精确复算：字符串转回 Decimal 与真值一位不差。
    assert Decimal(row.before_data["total_amount"]) == BIG