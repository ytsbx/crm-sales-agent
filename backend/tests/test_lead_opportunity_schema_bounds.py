"""B19-07 / B19-08：线索与商机入参边界回归（长度 / 名非空 / 批量上限）。

纯 schema 级断言：超长 / 空名 / 超量必须在进库前被 422 拒掉。
"""
import pytest
from pydantic import ValidationError

from app.modules.lead.schema import LeadBatchAssign, LeadCreate, LeadUpdate
from app.modules.opportunity.schema import (
    OpportunityCreate,
    OpportunityItemCreate,
    OpportunityUpdate,
)


def test_lead_create_field_lengths():
    assert LeadCreate(name="正常", mobile="13800000000").name == "正常"
    with pytest.raises(ValidationError):
        LeadCreate(name="正常", contact_name="x" * 65)
    with pytest.raises(ValidationError):
        LeadCreate(name="正常", source="x" * 33)


def test_lead_update_name_rejects_null_and_blank():
    with pytest.raises(ValidationError):
        LeadUpdate(name=None)
    with pytest.raises(ValidationError):
        LeadUpdate(name="  ")
    assert LeadUpdate(name="  新名  ").name == "新名"
    assert LeadUpdate(region="华东").name is None  # 不传=不改


def test_lead_batch_ids_bounded():
    with pytest.raises(ValidationError):
        LeadBatchAssign(lead_ids=[], owner_id=1)
    with pytest.raises(ValidationError):
        LeadBatchAssign(lead_ids=list(range(501)), owner_id=1)


def test_opportunity_title_and_lengths():
    with pytest.raises(ValidationError):
        OpportunityCreate(customer_id=1, title="   ")
    with pytest.raises(ValidationError):
        OpportunityCreate(customer_id=1, title="正常", competitor="x" * 129)
    with pytest.raises(ValidationError):
        OpportunityUpdate(title=None)
    assert OpportunityUpdate(title="  改  ").title == "改"


def test_opportunity_item_lengths():
    with pytest.raises(ValidationError):
        OpportunityItemCreate(sku_id=1, specification="x" * 201)
    with pytest.raises(ValidationError):
        OpportunityItemCreate(sku_id=1, color="x" * 65)
    assert OpportunityItemCreate(sku_id=1, color="红").color == "红"