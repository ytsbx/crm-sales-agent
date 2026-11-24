"""
B06–B10 复核复现（证伪用）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b06_b10_highlights.py -q

断言写「修复后应有的行为」；当前失败的条目 = 缺陷可复现。
不连数据库、不改业务代码，用最小替身。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.modules.erp.collection import normalize_item


# ---------------------------------------------------------------- B06-01
def test_b06_01_order_mapping_should_not_parent_itself():
    """订单是归属锚点，不该把 parent_key 写成自己的 external_id。

    当前 normalize_item 把 `order_key = dedupe_key`，_ensure_mapping 又把它
    写进 parent_key；人工匹配时 router 会拿这个 parent_key 去找“父订单映射”，
    找到的是这条自己，internal_id 还是空 → 409，订单永远匹配不上。
    """
    item = normalize_item(
        {"external_id": "SO-UNKNOWN", "currency": "CNY"},
        object_type="order",
        position=1,
    )
    # _ensure_mapping 的实际赋值是 record.order_key or record.parent_key
    assigned_parent = item["order_key"] or item["parent_key"]
    assert assigned_parent is None, (
        f"订单映射的 parent_key 被写成了自己 {assigned_parent!r}；"
        "人工匹配会查到这条自身并报“请先匹配父订单”"
    )


# ---------------------------------------------------------------- B06-02
def test_b06_02_assign_mapping_should_not_globally_replay():
    """人工匹配一条，不该顺带重放同一系统/店铺下的全部待匹配对象。"""
    import inspect

    from app.modules.erp import collection

    source = inspect.getsource(collection.assign_mapping)
    assert "replay_pending_mappings(\n        session,\n        system_type=mapping.system_type,\n        shop_id=mapping.shop_id,\n        object_type=mapping.object_type" in source, (
        "assign_mapping 调 replay_pending_mappings 时没有限定 object_type，"
        "会把同店铺所有类型一起重放"
    )


# ---------------------------------------------------------------- B06-03
def test_b06_03_foreign_currency_record_should_not_kill_whole_page():
    """trade_mode 明写“外部同步（ERP）不拦”：外币记录不能抛 422 拖死整页。

    当前 normalize_item 直接 reject_foreign_currency，collect_once 页级异常
    回滚整页并保留 page_token，一条 USD 记录会让这个来源永久卡住。
    """
    item = normalize_item(
        {"external_id": "SO-USD-1", "currency": "USD"},
        object_type="order",
        position=1,
    )
    assert item["currency"] == "USD", "外币记录应在采集层保留并标记，而不是抛错"


# ---------------------------------------------------------------- B06-04/05
def test_b06_04_order_payload_should_not_use_float_for_money():
    import inspect

    from app.modules.erp import service

    source = inspect.getsource(service.build_order_payload)
    assert "float(order.total_amount)" not in source, "出站总额仍在用 float"
    assert "float(item.unit_price)" not in source, "出站单价仍在用 float"


def test_b06_05_order_payload_should_include_fee_breakdown():
    """订单总额含运费/其他费用/折扣，报文只有行金额，行合计对不上总额。"""
    import inspect

    from app.modules.erp import service

    source = inspect.getsource(service.build_order_payload)
    for key in ("logistics_amount", "other_charge_amount", "discount_amount"):
        assert key in source, f"推单报文缺 {key}，ERP 无法复算总额"


# ---------------------------------------------------------------- B07-02
def _form(amount: str):
    return [
        {"id": "TextField_1VWK1245SNBQ80", "value": "HT-001"},
        {"id": "TextField_23HBI7G786FO70G1", "value": "测试合同"},
        {"id": "MoneyField_1", "name": "合同金额", "value": amount},
        {
            "id": "AttachmentField_8LHIG786FO70G14",
            "value": '[{"fileId":"any-file"}]',
        },
    ]


def test_b07_02_link_should_reject_mismatched_amount():
    """关联钉钉审批只验编号/名称/附件，金额对不上也能签成已通过。"""
    from app.modules.contract.customer_flow import verify_instance

    doc = SimpleNamespace(doc_no="HT-001", title="测试合同")
    cfg = {"process_code": "PROC-1", "field_map": {"amount": "MoneyField_1"}}
    data = {
        "processCode": "PROC-1",
        "originatorUserId": "u-1",
        "formComponentValues": _form("999999.00"),
    }
    with pytest.raises(AppError, match="金额"):
        verify_instance(data, cfg=cfg, doc=doc, user_id="u-1")


# ---------------------------------------------------------------- B07-03
def test_b07_03_contract_should_freeze_amount_at_generation():
    """合同正文是生成时快照，OA 金额却取实时报价版本 → 二者可漂移。"""
    from app.modules.contract.model import ContractDocument

    assert hasattr(ContractDocument, "amount"), (
        "ContractDocument 没有冻结金额列，OA/签署只能回查实时报价，生成后改价会漂移"
    )


# ---------------------------------------------------------------- B07-04
def test_b07_04_contract_create_should_require_request_key():
    from app.modules.contract.schema import DocumentCreate

    assert DocumentCreate.model_fields["request_key"].is_required(), (
        "合同创建 request_key 仍可选，不传键连点两次会生成两份合同"
    )


# ---------------------------------------------------------------- B09-01
def _oa_row(**kw):
    row = SimpleNamespace(
        id=1,
        status="approved",
        result={"business_stages": {}},
        submit_round=1,
        oa_type="sample",
    )
    for key, value in kw.items():
        setattr(row, key, value)
    return row


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


def test_b09_01_missing_stage_snapshot_should_not_fall_back_to_overall_approved():
    """传了 release_stage，但 result 里没有该阶段 → 应拒绝，不能降级成整体通过。"""
    from app.modules.oa.business_gates import require_latest_approved

    with pytest.raises(AppError):
        asyncio.run(
            require_latest_approved(
                _FakeSession(_oa_row()),
                kind="sample",
                business_id=1,
                release_stage="sample",
            )
        )


# ---------------------------------------------------------------- B09-02
def test_b09_02_approval_context_should_keep_decimal():
    """审批规则上下文把金额/毛利率转 float；默认 5 万档下无实际翻档，但口径应统一。"""
    import inspect

    from app.modules.approval import rules_engine

    source = inspect.getsource(rules_engine.build_context)
    assert "total_cny = float(" not in source, "审批上下文的总额仍是 float"
    assert "gross_margin = float(" not in source, "审批上下文的毛利率仍是 float"


# ---------------------------------------------------------------- B10-01
class _SampleSession:
    def __init__(self, sample):
        self.sample = sample

    async def get(self, *args, **kwargs):
        return self.sample

    async def flush(self):
        return None


def test_b10_01_sample_without_stage_snapshot_should_not_become_approved():
    """OA 只回整体 approved、没有 business_stages.sample 时，不能落成打样 approved。"""
    from app.modules.oa.sample import _apply_sample_summary

    sample = SimpleNamespace(
        id=1,
        status="pending",
        version=1,
        deleted_at=None,
        source_context={},
        approved_at=None,
        reject_reason=None,
    )
    row = _oa_row(id=9, inquiry_id=1, inquiry_version=1, instance_id="inst-1")
    row.synced_at = None
    row.updated_at = None
    row.error = None
    asyncio.run(_apply_sample_summary(_SampleSession(sample), row, {"id": "inst-1", "status": "approved"}))
    assert sample.status == "pending", (
        "缺 sample 阶段快照仍被落成 approved，后续发货/生产闸门会跟着放行"
    )
