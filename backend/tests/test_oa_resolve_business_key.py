"""核定（认领/重发/作废）按 `oa_type` 解析业务对象。

守 2026-10-16 放开的一条：`oa_instances.inquiry_id` 这一列是**复用的**
——询价存需求 id、打样存打样单 id、合同存合同文档 id。核定入口原先写死
"非 inquiry 直接 404"，于是合同与打样一旦落到「结果待人工核对」就**没有任何
出口**（而三个核定动作正是共享状态机为这个状态定义的唯一出路）；同时
`_verify_instance_source` 一律按需求去取业务号，合同/打样的合法认领也永远过不了。

这里只测**纯逻辑**：把 `inquiry_id` 解释成哪个对象、认哪些业务号。
数据范围分流（`ensure_oa_type_scope`）与请求键占用是接口层的另一件事。
"""
from datetime import UTC, datetime
from types import SimpleNamespace as Obj

import pytest

from app.core.errors import AppError
from app.modules.dingtalk.model import OaInstance
from app.modules.dingtalk.service import resolve_business_key


def _oa(oa_type, inquiry_id):
    return OaInstance(
        id=1, inquiry_id=inquiry_id, inquiry_version=1, oa_type=oa_type,
        idempotency_key=f"{inquiry_id}:1:{oa_type}:1", submit_round=1,
        process_code="PROC", originator_user_id="ding-1", instance_id="INST-1",
        status="needs_review", created_at=datetime.now(UTC),
    )


def _session(mapping):
    """按实体类返回预置对象；查不到就返回 None（与 `session.get` 一致）。"""

    async def _get(entity, ident, *args, **kwargs):
        return mapping.get(entity)

    return Obj(get=_get)


@pytest.mark.anyio
async def test_contract_key_uses_the_contract_number():
    """合同：业务号认主键与**合同编号**——钉钉模板里填的通常是编号。"""
    from app.modules.contract.model import ContractDocument

    doc = Obj(id=55, doc_no="CT-2026-001", deleted_at=None)
    key = await resolve_business_key(_session({ContractDocument: doc}), _oa("contract", 55))

    assert key.label == "合同"
    assert key.accepted_ids == {"55", "CT-2026-001"}
    assert "CT-2026-001" in key.display


@pytest.mark.anyio
async def test_sample_key_labels_it_as_a_sample_not_a_demand():
    from app.modules.sample.model import SampleRequest

    key = await resolve_business_key(
        _session({SampleRequest: Obj(id=7, status="pending")}), _oa("sample", 7)
    )

    assert key.label == "打样单"
    assert key.accepted_ids == {"7"}
    assert "打样单编号 7" in key.display


@pytest.mark.anyio
async def test_inquiry_key_still_uses_the_demand_number():
    from app.modules.inquiry.model import CustomInquiry

    key = await resolve_business_key(
        _session({CustomInquiry: Obj(id=9, inquiry_no="INQ-2026-001")}), _oa("inquiry", 9)
    )

    assert key.label == "需求"
    assert key.accepted_ids == {"9", "INQ-2026-001"}


@pytest.mark.anyio
async def test_missing_business_row_still_leaves_the_primary_key_accepted():
    """业务对象查不到（比如被删了）也不能崩，至少还能按主键核对。"""
    from app.modules.contract.model import ContractDocument

    key = await resolve_business_key(_session({ContractDocument: None}), _oa("contract", 55))

    assert key.accepted_ids == {"55"}


# ------------------------------------------------------------ 来源核对
#
# 认领是**人手输单号**，所以核对必须 fail-closed。这里只验"业务号比对"
# 这一项按 oa_type 取对的表：合同单拿合同编号能过、拿别人的编号过不了。


def _source_session():
    """按实体类返回预置对象（合同 55 = CT-2026-001、打样 7）。"""

    async def _get(entity, ident, *args, **kwargs):
        from app.modules.contract.model import ContractDocument
        from app.modules.inquiry.model import CustomInquiry
        from app.modules.sample.model import SampleRequest

        if entity is ContractDocument:
            return Obj(id=55, doc_no="CT-2026-001", deleted_at=None)
        if entity is SampleRequest:
            return Obj(id=7, status="pending")
        if entity is CustomInquiry:
            return None
        return None

    return Obj(get=_get)


@pytest.mark.anyio
async def test_contract_adopt_accepts_the_contract_number():
    """合同单的来源核对要看**合同编号**，而不是拿别的表的主键去比。"""
    from app.modules.dingtalk.service import _verify_instance_source

    await _verify_instance_source(_source_session(), _oa("contract", 55), {
        "processCode": "PROC", "originatorUserId": "ding-1", "businessId": "CT-2026-001",
    })


@pytest.mark.anyio
async def test_contract_adopt_rejects_a_foreign_business_number():
    from app.modules.dingtalk.service import _verify_instance_source

    with pytest.raises(AppError) as caught:
        await _verify_instance_source(_source_session(), _oa("contract", 55), {
            "processCode": "PROC", "originatorUserId": "ding-1", "businessId": "CT-9999",
        })

    assert "合同" in caught.value.message
    assert "对不上" in caught.value.message


@pytest.mark.anyio
async def test_sample_adopt_accepts_the_sample_id():
    from app.modules.dingtalk.service import _verify_instance_source

    await _verify_instance_source(_source_session(), _oa("sample", 7), {
        "processCode": "PROC", "originatorUserId": "ding-1", "businessId": "7",
    })


@pytest.mark.anyio
async def test_verify_still_rejects_a_wrong_template():
    """模板对不上必须拒——与业务号无关，这条是既有的 fail-closed 口径。"""
    from app.modules.dingtalk.service import _verify_instance_source

    with pytest.raises(AppError) as caught:
        await _verify_instance_source(_source_session(), _oa("contract", 55), {
            "processCode": "OTHER", "originatorUserId": "ding-1", "businessId": "CT-2026-001",
        })

    assert "模板" in caught.value.message
