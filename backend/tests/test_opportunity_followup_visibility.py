"""商机跟进子列表不能绕过系统过程记录的原单授权。"""
from datetime import UTC, datetime

import pytest

from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.router import get_followup, list_opportunity_followups
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest
from tests.conftest import SyncSessionAsAsync, make_user


@pytest.fixture
def followup_db(db_session):
    for model in (FollowUp, SampleRequest):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add(Customer(id=1, name="本人客户", owner_id=101))
    db_session.add(OpportunityStage(id=1, code="open", name="初步沟通"))
    db_session.flush()
    db_session.add(Opportunity(id=1, title="本人商机", customer_id=1, stage_id=1, owner_id=101))
    db_session.flush()
    db_session.add(Quote(id=1, quote_no="Q-PRIVATE", customer_id=1, opportunity_id=1, owner_id=101))
    db_session.add_all([
        FollowUp(id=1, customer_id=1, opportunity_id=1, owner_id=999,
                 followup_type="电话", content="客户沟通记录"),
        FollowUp(id=2, customer_id=1, opportunity_id=1, quote_id=1, owner_id=101,
                 followup_type="系统", content="报价已提交审批，金额 9876.54"),
    ])
    db_session.flush()
    return db_session


@pytest.mark.anyio
@pytest.mark.parametrize("restriction", ["missing_permission", "outside_scope", "deleted_source"])
async def test_opportunity_sublist_hides_system_record_denied_by_detail(followup_db, restriction):
    db = followup_db
    permissions = {"followup:view"}
    if restriction != "missing_permission":
        permissions.add("quote:view")
    if restriction == "outside_scope":
        db.get(Quote, 1).owner_id = 999
    if restriction == "deleted_source":
        db.get(Quote, 1).deleted_at = datetime.now(UTC)
    db.flush()
    user = make_user(101, permissions=permissions)
    session = SyncSessionAsAsync(db)
    with pytest.raises(AppError) as error:
        await get_followup(2, user=user, session=session)
    assert error.value.http_status == 403
    result = await list_opportunity_followups(1, user=user, session=session)
    assert [row["id"] for row in result["data"]] == [1]


@pytest.mark.anyio
@pytest.mark.parametrize("admin", [False, True])
async def test_authorized_sources_and_collaborative_manual_records_remain_visible(followup_db, admin):
    user = make_user(101, permissions={"followup:view"} if admin else {"followup:view", "quote:view"},
                     roles=("admin",) if admin else ("salesperson",), data_scope="all" if admin else "self")
    result = await list_opportunity_followups(1, user=user, session=SyncSessionAsAsync(followup_db))
    assert [row["id"] for row in result["data"]] == [2, 1]
