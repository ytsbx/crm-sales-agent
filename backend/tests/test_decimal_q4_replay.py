"""Q4 决策2 边界回归：幂等回放快照存精确原文，大额一分不差；老 dict 快照走老路。

覆盖：16 位大金额 complete→reserve 回放逐位精确（旧 float 快照会差一分）；
datetime 归一字符串可往返；历史 dict 行（float 形状）原样回放、契约不变。
"""
import json
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from conftest import SyncSessionAsAsync

from app.core import idempotency
from app.core.jsonio import exact_json_dumps

BIG = D("99999999999999.99")


@pytest.mark.anyio
async def test_replay_big_amount_is_exact_to_the_cent(db_session):
    idempotency.RequestKey.__table__.create(db_session.get_bind(), checkfirst=True)
    session = SyncSessionAsAsync(db_session)
    payload = {"name": "大单"}
    first = await idempotency.reserve(
        session, user_id=1, action="order:create", request_key="k-big", payload=payload)
    await idempotency.complete(
        session, first,
        result_payload={"total_amount": BIG, "at": datetime(2026, 11, 24, 10, 0, tzinfo=UTC)},
        result_id=1)
    stored = first.row.result_payload
    assert isinstance(stored, str)
    assert "99999999999999.99" in stored  # 原文一位不丢
    replay = await idempotency.reserve(
        session, user_id=1, action="order:create", request_key="k-big", payload=payload)
    assert replay.should_replay is True
    assert replay.replay_payload["total_amount"] == BIG
    assert replay.replay_payload["at"] == "2026-11-24T10:00:00+00:00"
    wire = exact_json_dumps(replay.replay_payload)
    back = json.loads(wire, parse_float=D, parse_int=D)
    assert back["total_amount"] == BIG


@pytest.mark.anyio
async def test_legacy_dict_snapshot_replays_unchanged(db_session):
    idempotency.RequestKey.__table__.create(db_session.get_bind(), checkfirst=True)
    session = SyncSessionAsAsync(db_session)
    payload = {"name": "老单"}
    row = idempotency.RequestKey(
        user_id=1, action="order:create", request_key="k-legacy",
        request_hash=idempotency.payload_hash(payload), status="done",
        result_payload={"total_amount": 12.5}, result_id=2)
    db_session.add(row)
    db_session.flush()
    replay = await idempotency.reserve(
        session, user_id=1, action="order:create", request_key="k-legacy", payload=payload)
    assert replay.should_replay is True
    # 历史行不重写：float 形状原样回放，老契约零变化
    assert replay.replay_payload == {"total_amount": 12.5}
    assert type(replay.replay_payload["total_amount"]) is float


def test_json_safe_float_copy_mode_removed():
    """老按钮拆除（主人 2026-11-24 批准）：False 显式报错，不再静默转 float。"""
    from decimal import Decimal as D

    import pytest

    from app.core.audit import json_safe

    with pytest.raises(ValueError, match="已拆除"):
        json_safe({"amount": D("99999999999999.99")}, decimal_as_str=False)
    assert json_safe(D("1.90")) == "1.90"
    assert json_safe(D("1.90"), decimal_as_str=None) == D("1.90")