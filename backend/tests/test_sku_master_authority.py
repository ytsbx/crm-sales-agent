"""在产 SKU 的权威字段、来源时间与差异确认（第八批 §8.14）。

用例守的是交接说明 §8.14 的验收项，走**真函数 + 内存 SQLite**：

1. 外部来源的值**不自动写回**本地 SKU（差异进队列）；
2. 增量空值不抹人工销售资料；要采纳空值必须显式确认并写依据；
3. 三系统同名不同码**不自动合并**（挂差异待人工裁定）；
4. 单位/包装冲突进入待确认队列；
5. 改码、停用不破坏历史（老编码仍能反查、历史确认版本不动）；
6. 来源未核实显示"待核实"、字段权威未拍板显示"未拍板"；
7. 人工确认会生成可回溯的主数据版本，正式报价前有闸门函数把关；
8. 待匹配的外部身份在本地补建 SKU 后可重放（不需要重新取数）。

真实来源（简道云/聚水潭字段字典与授权）还没拿到，所以这里验证的是**机制**：
数据模型、差异队列、确认动作与版本快照。真实对接列为待外部验收。
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine

from app.core.base import Base
from app.core.errors import AppError
from app.modules.integration.model import (
    ExternalSourceRegistry,
    IntegrationDiff,
)
from app.modules.integration.vocab import (
    AUTH_PENDING,
    AUTH_UNVERIFIED,
    DIFF_CODE_RENAME,
    DIFF_NULL_OVERWRITE,
    DIFF_PACKAGE_CONFLICT,
    DIFF_SAME_NAME_DIFF_CODE,
    DIFF_STOPPED_SOURCE,
    DIFF_UNIT_CONFLICT,
    DIFF_UNMATCHED_SKU_SOURCE,
    IDENTITY_RENAMED,
    IDENTITY_STOPPED,
    RESOLUTION_DISABLE_LOCAL,
    RESOLUTION_KEEP_LOCAL,
    RESOLUTION_RENAME_LOCAL,
    RESOLUTION_TAKE_EXTERNAL,
)
from app.modules.product import master as m
from app.modules.product.model import (
    Product,
    Sku,
    SkuFieldAuthority,
    SkuIdentitySource,
    SkuMasterVersion,
)

JIANDAO = "JIANDAOYUN"
JUSHUITAN = "JUSHUITAN"
NOW = datetime(2026, 10, 6, tzinfo=UTC)


def _engine():
    engine = make_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            Product.__table__,
            Sku.__table__,
            SkuIdentitySource.__table__,
            SkuFieldAuthority.__table__,
            SkuMasterVersion.__table__,
            IntegrationDiff.__table__,
            # 来源是否"已核实"读的是这张台账（默认未核实）。
            ExternalSourceRegistry.__table__,
        ],
    )
    return engine


def _session():
    return SyncSessionAsAsync(Session(_engine()))


def _sku(session, *, code: str, name: str, **fields) -> int:
    """建一个本地 SKU（替身会话的 flush 是协程，所以要 asyncio.run）。"""
    product = Product(name=f"产品-{code}")
    session.add(product)
    asyncio.run(session.flush())
    row = Sku(product_id=product.id, sku_code=code, name=name, **fields)
    session.add(row)
    asyncio.run(session.flush())
    return row.id


def _ingest(session, **kwargs):
    params = {
        "system_type": JIANDAO,
        "external_code": "A-1",
        "external_name": "甲件",
        "fields": {},
        "now": NOW,
    }
    params.update(kwargs)
    return asyncio.run(m.ingest_external_sku(session, **params))


def _get(session, model, pk):
    """替身会话的 get 是协程：测试里显式跑一次，别依赖隐式 await。"""
    return asyncio.run(session.get(model, pk))


def _diffs(session, **kwargs):
    return asyncio.run(m.list_sku_diffs(session, page_size=50, **kwargs))


def _confirm(session, diff_id, resolution, note=None):
    diff = asyncio.run(session.get(IntegrationDiff, diff_id))
    return asyncio.run(
        m.confirm_sku_diff(session, diff, resolution=resolution, note=note, operator_id=9, now=NOW)
    )


# ---------------------------------------------------------------- 不自动覆盖


def test_external_value_never_overwrites_local_sku():
    """外部来源给了不同的单位/装箱数：本地一个字节都不改，差异进队列。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件", carton_qty=10)

    result = _ingest(session, fields={"unit": "箱", "carton_qty": 20})

    sku = _get(session, Sku, sku_id)
    assert sku.unit == "件", "外部值不得自动覆盖本地单位"
    assert sku.carton_qty == 10
    assert result["applied_to_local"] is False

    diffs = _diffs(session)[0]
    kinds = {row["diff_type"] for row in diffs}
    assert kinds == {DIFF_UNIT_CONFLICT, DIFF_PACKAGE_CONFLICT}
    unit_diff = next(row for row in diffs if row["diff_type"] == DIFF_UNIT_CONFLICT)
    assert unit_diff["current_value"] == "件"
    assert unit_diff["incoming_value"] == "箱"
    assert unit_diff["allowed_resolutions"] == [
        RESOLUTION_KEEP_LOCAL,
        RESOLUTION_TAKE_EXTERNAL,
        "manual",
    ]


def test_unverified_source_and_unclaimed_authority_are_visible():
    """来源未核实显示"待核实"，字段权威默认"未拍板"（不默认任一系统为主）。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件")
    _ingest(session, fields={"unit": "箱"})

    overview = asyncio.run(m.sku_master_overview(session, sku_id))
    unit = next(item for item in overview["fields"] if item["field_name"] == "unit")
    assert unit["source_status"] == "待核实"
    assert unit["source_verified"] is False
    assert unit["authority"] is None
    assert unit["authority_label"] == "未拍板"
    assert unit["status"] == AUTH_PENDING
    assert overview["pending_diff_count"] == 1
    assert overview["latest_confirmed_version"] == 0

    untouched = next(item for item in overview["fields"] if item["field_name"] == "color")
    assert untouched["status"] == AUTH_UNVERIFIED
    assert untouched["source_system"] is None


def test_null_increment_does_not_erase_manual_data_and_needs_evidence():
    """增量空值不抹人工资料；要采纳空值必须显式确认并写依据。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", specification="600×400")

    _ingest(session, fields={"specification": None, "name": ""})

    sku = _get(session, Sku, sku_id)
    assert sku.specification == "600×400", "空值不得抹掉本地资料"
    assert sku.name == "甲件"

    diffs = _diffs(session)[0]
    assert {row["diff_type"] for row in diffs} == {DIFF_NULL_OVERWRITE}
    assert diffs[0]["requires_note"] is True
    assert diffs[0]["allowed_resolutions"] == [RESOLUTION_KEEP_LOCAL, RESOLUTION_TAKE_EXTERNAL]

    with pytest.raises(AppError) as caught:
        _confirm(session, diffs[0]["id"], RESOLUTION_TAKE_EXTERNAL, note="   ")
    assert "依据" in caught.value.message
    assert _get(session, Sku, sku_id).specification == "600×400"


# ---------------------------------------------------------------- 同名不同码


def test_same_name_different_code_is_not_auto_merged():
    """三系统同名不同码：只挂待确认差异，**绝不自动合并**成一个 SKU。"""
    session = _session()
    sku_id = _sku(session, code="B-1", name="乙件", unit="件")

    result = _ingest(
        session,
        system_type=JUSHUITAN,
        external_code="B-9",
        external_name="乙件",
        fields={"unit": "箱"},
    )

    identity = result["identity"]
    assert identity["sku_id"] is None, "同名不同码不得自动挂到本地 SKU 上"
    assert identity["match_status"] == "conflict"

    diffs = _diffs(session)[0]
    # 只出**一条**更具体的"同名不同码"差异：再叠一条泛化的"待匹配"，
    # 同一个问题在队列里就是两条，人很快就不看这个队列了。
    assert [row["diff_type"] for row in diffs] == [DIFF_SAME_NAME_DIFF_CODE]
    assert diffs[0]["current_value"] == ["B-1"]
    assert diffs[0]["incoming_value"] == "B-9"
    assert diffs[0]["allowed_resolutions"] == [RESOLUTION_KEEP_LOCAL, "manual"]
    assert diffs[0]["requires_note"] is True

    # 本地 SKU 没有被改动，也没有多出第二条 SKU
    assert _get(session, Sku, sku_id).unit == "件"
    assert asyncio.run(session.execute(select(Sku))).scalars().all().__len__() == 1


# ---------------------------------------------------------------- 改码与停用


def test_code_rename_keeps_old_code_traceable_and_does_not_touch_local():
    """来源改码：老身份行保留成历史；本地编码要改必须人工确认。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件")
    _ingest(session, fields={"unit": "件"})
    source = asyncio.run(m.list_identity_sources(session, page_size=10))[0][0]

    result = asyncio.run(
        m.rename_identity_source(
            session,
            asyncio.run(m.get_identity_source(session, source["id"])),
            new_external_code="A-2",
            operator_id=9,
            now=NOW,
        )
    )
    assert result["applied_to_local"] is False
    assert _get(session, Sku, sku_id).sku_code == "A-1", "本地编码不得自动改"

    identities = asyncio.run(m.list_identity_sources(session, page_size=10))[0]
    assert len(identities) == 2
    old = next(row for row in identities if row["external_code"] == "A-1")
    new = next(row for row in identities if row["external_code"] == "A-2")
    assert old["match_status"] == IDENTITY_RENAMED
    assert old["superseded_by_code"] == "A-2"
    assert old["sku_id"] == sku_id and new["sku_id"] == sku_id, "老编码仍能反查到同一个 SKU"

    rename_diff = next(
        row for row in _diffs(session)[0] if row["diff_type"] == DIFF_CODE_RENAME
    )
    outcome = _confirm(session, rename_diff["id"], RESOLUTION_RENAME_LOCAL, note="来源改用新码")
    assert outcome["applied_to_local"] is True
    assert _get(session, Sku, sku_id).sku_code == "A-2"
    # 老编码的历史身份行还在，历史单据上印的老编码依然解释得通
    assert (
        asyncio.run(m.list_identity_sources(session, match_status=IDENTITY_RENAMED))[0][0][
            "external_code"
        ]
        == "A-1"
    )


def test_stopped_source_needs_confirmation_and_keeps_history():
    """来源停用：本地状态要人工确认才改；不删除、历史确认版本不动。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件")
    _ingest(session, fields={"unit": "件"})
    source = asyncio.run(m.list_identity_sources(session, page_size=10))[0][0]

    stopped = asyncio.run(
        m.mark_identity_stopped(
            session,
            asyncio.run(m.get_identity_source(session, source["id"])),
            note="来源说这个货停产了",
            operator_id=9,
            now=NOW,
        )
    )
    assert stopped["applied_to_local"] is False
    assert _get(session, Sku, sku_id).status == "active", "停用不得自动生效"

    diff = next(row for row in _diffs(session)[0] if row["diff_type"] == DIFF_STOPPED_SOURCE)
    assert diff["allowed_resolutions"] == [RESOLUTION_KEEP_LOCAL, RESOLUTION_DISABLE_LOCAL]
    outcome = _confirm(session, diff["id"], RESOLUTION_DISABLE_LOCAL, note="确认停产")
    assert outcome["applied_to_local"] is True
    assert _get(session, Sku, sku_id).status == "disabled"
    # 行还在（不是删除），身份台账也还查得到
    assert asyncio.run(m.list_identity_sources(session, page_size=10))[1] == 1
    identities = asyncio.run(m.list_identity_sources(session, page_size=10))[0]
    assert identities[0]["match_status"] == IDENTITY_STOPPED


# ---------------------------------------------------------------- 确认与版本


def test_confirm_creates_traceable_master_version():
    """人工确认生成版本快照，"正式报价用哪一版"可回溯。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件", carton_qty=10)
    _ingest(session, fields={"unit": "箱", "carton_qty": 20})
    diffs = _diffs(session)[0]
    unit_diff = next(row for row in diffs if row["diff_type"] == DIFF_UNIT_CONFLICT)
    pack_diff = next(row for row in diffs if row["diff_type"] == DIFF_PACKAGE_CONFLICT)

    # ① 保留本地：确认的是"当前本地值"，不改 SKU
    kept = _confirm(session, unit_diff["id"], RESOLUTION_KEEP_LOCAL, note="以本地口径为准")
    assert kept["applied_to_local"] is False
    assert kept["version"]["version_no"] == 1
    assert kept["version"]["values"]["unit"] == "件"
    assert _get(session, Sku, sku_id).unit == "件"

    # ② 采纳外部：写本地并再生成一版
    taken = _confirm(session, pack_diff["id"], RESOLUTION_TAKE_EXTERNAL, note="按来源的装箱数")
    assert taken["applied_to_local"] is True
    assert _get(session, Sku, sku_id).carton_qty == 20
    assert taken["version"]["version_no"] == 2
    assert taken["version"]["values"]["carton_qty"] == 20
    assert taken["version"]["values"]["unit"] == "件", "上一版确认过的字段要一起进快照"

    version = asyncio.run(m.confirmed_master_version(session, sku_id))
    assert version["version_no"] == 2
    assert version["source_summary"]["unit"]["source_system"] == JIANDAO
    assert version["source_summary"]["unit"]["source_verified"] is False

    overview = asyncio.run(m.sku_master_overview(session, sku_id))
    unit_field = next(item for item in overview["fields"] if item["field_name"] == "unit")
    assert unit_field["confirmed_value"] == "件"
    assert unit_field["confirmed_version"] == 1
    assert unit_field["status"] == "confirmed"


def test_require_confirmed_master_blocks_unconfirmed_fields():
    """正式报价闸门：没确认过的字段必须明确被挡住并说清是哪些。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件")
    _ingest(session, fields={"unit": "箱"})
    unit_diff = next(
        row for row in _diffs(session)[0] if row["diff_type"] == DIFF_UNIT_CONFLICT
    )

    with pytest.raises(AppError) as caught:
        asyncio.run(m.require_confirmed_master(session, sku_id, ["unit"]))
    assert "还没有人工确认" in caught.value.message

    _confirm(session, unit_diff["id"], RESOLUTION_KEEP_LOCAL, note="以本地口径为准")
    ok = asyncio.run(m.require_confirmed_master(session, sku_id, ["unit"]))
    assert ok["values"] == {"unit": "件"}
    assert ok["version"]["version_no"] == 1

    with pytest.raises(AppError) as caught:
        asyncio.run(m.require_confirmed_master(session, sku_id, ["unit", "specification"]))
    assert "specification" in caught.value.message


def test_field_authority_is_recorded_only_when_someone_claims_it():
    """字段权威归属：默认空；登记谁就记谁与时间，撤回也要留痕。"""
    session = _session()
    sku_id = _sku(session, code="A-1", name="甲件", unit="件")

    set_result = asyncio.run(
        m.set_field_authority(
            session,
            sku_id=sku_id,
            field_name="unit",
            authority="CRM",
            operator_id=9,
            now=NOW,
        )
    )
    assert set_result["authority"] == "CRM"
    row = (
        asyncio.run(
            session.execute(
                select(SkuFieldAuthority).where(SkuFieldAuthority.sku_id == sku_id)
            )
        )
        .scalars()
        .first()
    )
    assert row.authority_set_by == 9
    # SQLite 不保留时区，只断言"确实记下了时间"（口径由服务层统一按 UTC 写）。
    assert row.authority_set_at is not None

    cleared = asyncio.run(
        m.set_field_authority(
            session, sku_id=sku_id, field_name="unit", authority=None, operator_id=9, now=NOW
        )
    )
    assert cleared["authority"] is None
    assert cleared["authority_label"] == "未拍板"

    with pytest.raises(AppError):
        asyncio.run(
            m.set_field_authority(
                session,
                sku_id=sku_id,
                field_name="内部备注",
                authority="CRM",
                operator_id=9,
                now=NOW,
            )
        )


def test_unknown_field_name_is_rejected_before_any_write():
    """字段名不在白名单：写任何东西之前就报错（避免半截状态）。"""
    session = _session()
    _sku(session, code="A-1", name="甲件")
    with pytest.raises(AppError) as caught:
        _ingest(session, fields={"unit": "箱", "internal_note": "别写我"})
    assert "internal_note" in caught.value.message
    assert (
        asyncio.run(session.execute(select(SkuIdentitySource))).scalars().all() == []
    ), "字段校验失败时不得留下半截身份记录"


# ---------------------------------------------------------------- 待匹配与重放


def test_unmatched_source_replays_after_local_sku_created():
    """本地还没有这条 SKU：先待匹配并留存来源值，补建后重放即落成字段权威。"""
    session = _session()
    result = _ingest(session, external_code="NEW-1", external_name="新件", fields={"unit": "箱"})

    assert result["identity"]["sku_id"] is None
    diffs = _diffs(session)[0]
    assert [row["diff_type"] for row in diffs] == [DIFF_UNMATCHED_SKU_SOURCE]
    assert diffs[0]["allowed_resolutions"] == ["manual"]

    sku_id = _sku(session, code="NEW-1", name="新件", unit="件")
    replay = asyncio.run(
        m.replay_identity_source(
            session,
            asyncio.run(m.get_identity_source(session, result["identity"]["id"])),
            operator_id=9,
            now=NOW,
        )
    )
    assert replay["replayed"] is True
    assert replay["identity"]["sku_id"] == sku_id
    assert replay["counts"]["conflicts"] == 1, "留存下来的来源值要落成差异"

    overview = asyncio.run(m.sku_master_overview(session, sku_id))
    unit = next(item for item in overview["fields"] if item["field_name"] == "unit")
    assert unit["source_value"] == "箱"
    assert unit["source_status"] == "待核实"
    assert any(row["diff_type"] == DIFF_UNIT_CONFLICT for row in overview["pending_diffs"])


def test_ingest_is_idempotent_and_updates_source_time():
    """同一来源重复上报：身份只有一行，来源时间刷新到最新。"""
    session = _session()
    _sku(session, code="A-1", name="甲件", unit="件")
    _ingest(session, fields={"unit": "件"})
    again = _ingest(session, fields={"unit": "件"}, source_updated_at="2026-10-05T08:00:00")

    identities = asyncio.run(m.list_identity_sources(session, page_size=10))[0]
    assert len(identities) == 1
    assert again["unchanged"] == 1
    assert identities[0]["source_updated_at"] is not None
    _, total = _diffs(session)
    assert total == 0, "值一致时不该出差异"
