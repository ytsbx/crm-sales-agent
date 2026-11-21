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


@pytest.mark.anyio
@pytest.mark.parametrize('admin', [False, True])
async def test_deleted_sample_hides_system_fact_and_file_then_restore_reveals(followup_db, admin):
    from app.modules.file.access import can_access_file
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.followup.router import list_followups
    from app.modules.followup.visibility import system_source_filter
    from sqlalchemy import select

    db = followup_db
    for model in (FileRecord, BusinessFile):
        model.__table__.create(db.get_bind(), checkfirst=True)
    db.delete(db.get(FollowUp, 2))
    db.add(SampleRequest(id=1, customer_id=1, owner_id=101, status='pending', created_at=datetime.now(UTC)))
    db.add_all([
        FollowUp(id=3, customer_id=1, opportunity_id=1, sample_id=1, quote_id=1, owner_id=101,
                 followup_type='系统', content='打样自动过程记录'),
        FollowUp(id=4, customer_id=1, opportunity_id=1, sample_id=1, owner_id=101,
                 followup_type='电话', content='人工跟进仍保留'),
        FileRecord(id=3, object_key='isolated.txt', file_name='isolated.txt', uploaded_by=101),
        BusinessFile(id=3, file_id=3, business_type='followup', business_id=3),
    ])
    db.flush()
    user = make_user(101, permissions={'followup:view', 'sample:view', 'quote:view', 'customer:view'},
                     roles=('admin',) if admin else ('salesperson',), data_scope='all' if admin else 'self')
    session = SyncSessionAsAsync(db)
    for deleted in (False, True, False):
        db.get(SampleRequest, 1).deleted_at = datetime.now(UTC) if deleted else None
        db.flush()
        expected = [4, 1] if deleted else [4, 3, 1]
        result = await list_opportunity_followups(1, user=user, session=session)
        assert [row['id'] for row in result['data']] == expected
        # Customer timeline/overview use this same source filter before LIMIT.
        ids = (await session.execute(select(FollowUp.id).where(
            FollowUp.customer_id == 1, await system_source_filter(session, user)
        ).order_by(FollowUp.id.desc()))).scalars().all()
        assert ids == expected
        page = await list_followups(customer_id=1, opportunity_id=None, lead_id=None, owner_id=None,
                                    page=2, page_size=1, user=user, session=session)
        assert page['data']['total'] == len(expected)
        assert [row['id'] for row in page['data']['items']] == [expected[1]]
        assert await can_access_file(session, user, 3) is (not deleted)
        if deleted:
            with pytest.raises(AppError) as error:
                await get_followup(3, user=user, session=session)
            assert error.value.http_status == 403
            # A separate visible mount still grants access to a shared file.
            shared = BusinessFile(file_id=3, business_type='customer', business_id=1)
            db.add(shared)
            db.flush()
            assert await can_access_file(session, user, 3) is True
            db.delete(shared)
            db.flush()
            assert await can_access_file(session, user, 3) is False
        else:
            assert (await get_followup(3, user=user, session=session))['data']['id'] == 3
