"""交接首份底账必须足够让中断任务恢复；仅独立内存SQLite。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.user.model import User
from app.modules.wecom import service
from app.modules.wecom.model import TRANSFER_KIND_LABEL, WeComSyncJob, WeComTransferItem
from conftest import SyncSessionAsAsync, make_user


@pytest.mark.anyio
async def test_transfer_ledger_preserves_retry_identity_before_external_or_crm_work(db_session, monkeypatch):
    raw = db_session
    # 此fixture不默认创建企微表；不接触任何外部数据库。
    for model in (WeComSyncJob, WeComTransferItem):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    for uid in (1, 2, 3):
        raw.add(User(id=uid, name=f'隔离人员{uid}', username=f'ledger-{uid}', password_hash='not-a-login-hash', status='active'))
    raw.commit()

    async def scope(*args, **kwargs):
        result = {kind: [] for kind in TRANSFER_KIND_LABEL}
        result['order'] = [{'business_id': 17, 'label': '隔离订单', 'blocked_reason': None}]
        return result

    def interrupted():
        raise RuntimeError('isolated interruption after initial ledger commit')

    monkeypatch.setattr(service, 'collect_transfer_scope', scope)
    monkeypatch.setattr(service.wecom_client, 'get_client', interrupted)
    with pytest.raises(RuntimeError, match='isolated interruption'):
        await service.transfer_relations(
            SyncSessionAsAsync(raw), user=SimpleNamespace(id=1),
            handover_user_id=1, takeover_user_id=2, transfer_wecom=True,
            item_assignees={'order:17': 3},
        )
    raw.rollback()
    job = raw.scalars(select(WeComSyncJob)).one()
    assert job.status == 'running'
    assert job.detail['handover_id'] == 1
    assert job.detail['takeover_id'] == 2
    assert job.detail['handover'] == '隔离人员1'
    assert job.detail['takeover'] == '隔离人员2'
    assert job.detail['item_assignees'] == {'order:17': '隔离人员3'}
    item = raw.scalars(select(WeComTransferItem)).one()
    assert (item.job_id, item.kind, item.business_id, item.to_owner_id, item.crm_status) == (job.id, 'order', 17, 3, 'pending')


@pytest.mark.anyio
@pytest.mark.parametrize('transfer_wecom', [False, True])
async def test_initial_ledger_records_explicit_external_transfer_choice(db_session, monkeypatch, transfer_wecom):
    raw = db_session
    for model in (WeComSyncJob, WeComTransferItem):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    for uid in (1, 2):
        raw.add(User(id=uid, name=f'隔离人员{uid}', username=f'choice-{uid}', password_hash='not-a-login-hash', status='active'))
    raw.commit()

    async def scope(*args, **kwargs):
        result = {kind: [] for kind in TRANSFER_KIND_LABEL}
        result['wecom_relation'] = [{'business_id': 19, 'label': '隔离关系', 'blocked_reason': None}]
        return result

    class InterruptedSession(SyncSessionAsAsync):
        async def commit(self):
            await super().commit()
            raise RuntimeError('isolated interruption after first commit')

    monkeypatch.setattr(service, 'collect_transfer_scope', scope)
    with pytest.raises(RuntimeError, match='isolated interruption'):
        await service.transfer_relations(
            InterruptedSession(raw), user=SimpleNamespace(id=1),
            handover_user_id=1, takeover_user_id=2, transfer_wecom=transfer_wecom,
        )
    raw.rollback()
    item = raw.scalars(select(WeComTransferItem)).one()
    assert item.crm_status == 'not_applicable'
    assert item.wecom_status == ('pending' if transfer_wecom else 'skipped')
    assert item.wecom_error == (None if transfer_wecom else '本次未要求转接企微关系')


# ---- B15-04：NULL owner 重试不得改派 ----------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['opportunity', 'task', 'sample', 'order', 'quote'])
async def test_retry_null_owner_must_not_reassign(db_session, kind):
    """并发清空负责人后，重试应报冲突而非把项改给接手人。"""
    from app.modules.opportunity.model import Opportunity
    from app.modules.task.model import Task
    from app.modules.sample.model import SampleRequest
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote

    raw = db_session
    for model in (WeComSyncJob, WeComTransferItem):
        model.__table__.create(raw.get_bind(), checkfirst=True)

    models = {
        'opportunity': Opportunity, 'task': Task, 'sample': SampleRequest,
        'order': SalesOrder, 'quote': Quote,
    }
    model = models[kind]
    model.__table__.create(raw.get_bind(), checkfirst=True)

    for uid in (1, 2):
        raw.add(User(id=uid, name=f'隔离{uid}', username=f'null-owner-{uid}',
                     password_hash='x', status='active'))
    # 业务行 owner_id=None：模拟并发清空（放回公海/取消）
    kwargs = {'id': 10, 'owner_id': None}
    if kind == 'opportunity':
        kwargs.update(title='隔离商机', customer_id=1, stage_id=1)
    elif kind == 'task':
        kwargs.update(title='隔离任务')
    elif kind == 'sample':
        pass  # SampleRequest 只需 id + owner_id
    elif kind == 'order':
        kwargs.update(order_no='O-ISO', customer_id=1, total_amount=100)
    elif kind == 'quote':
        kwargs.update(quote_no='Q-ISO', customer_id=1)
    raw.add(model(**kwargs))
    raw.add(WeComSyncJob(
        id=1, job_type='transfer', status='partial', operator_id=1,
        detail={'handover_id': 1, 'takeover_id': 2},
    ))
    raw.add(WeComTransferItem(
        id=1, job_id=1, kind=kind, business_id=10,
        from_owner_id=1, to_owner_id=2, to_owner_name='隔离2',
        crm_status='pending', wecom_status='skipped',
    ))
    raw.commit()

    await service.retry_transfer(
        SyncSessionAsAsync(raw), user=make_user(1, roles=('admin',)), job_id=1,
    )
    raw.expire_all()
    item = raw.get(WeComTransferItem, 1)
    row = raw.get(model, 10)
    # 核心断言：owner 仍为 None（未被改派），状态为 failed（冲突）
    assert row.owner_id is None, f'{kind}: NULL owner 被改派给了接手人'
    assert item.crm_status == 'failed'
    assert '已经不是' in (item.crm_error or '')


@pytest.mark.anyio
async def test_retry_null_production_owner_must_not_reassign(db_session):
    """sample_production 的 production_owner_id=None 同理。"""
    from app.modules.sample.model import SampleRequest

    raw = db_session
    for model in (WeComSyncJob, WeComTransferItem):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    SampleRequest.__table__.create(raw.get_bind(), checkfirst=True)

    for uid in (1, 2):
        raw.add(User(id=uid, name=f'隔离{uid}', username=f'null-prod-{uid}',
                     password_hash='x', status='active'))
    raw.add(SampleRequest(id=10, owner_id=1, production_owner_id=None))
    raw.add(WeComSyncJob(
        id=1, job_type='transfer', status='partial', operator_id=1,
        detail={'handover_id': 1, 'takeover_id': 2},
    ))
    raw.add(WeComTransferItem(
        id=1, job_id=1, kind='sample_production', business_id=10,
        from_owner_id=1, to_owner_id=2, to_owner_name='隔离2',
        crm_status='pending', wecom_status='skipped',
    ))
    raw.commit()

    await service.retry_transfer(
        SyncSessionAsAsync(raw), user=make_user(1, roles=('admin',)), job_id=1,
    )
    raw.expire_all()
    item = raw.get(WeComTransferItem, 1)
    row = raw.get(SampleRequest, 10)
    assert row.production_owner_id is None
    assert item.crm_status == 'failed'


# ---- B15-05：接管人未绑企微 → 预览警告 + 执行跳过 ----------------------------

@pytest.mark.anyio
async def test_preview_warns_when_takeover_lacks_wecom(db_session):
    from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.task.model import Task
    from app.modules.sample.model import SampleRequest
    from app.modules.order.model import SalesOrder, OrderDraft
    from app.modules.quote.model import Quote

    raw = db_session
    for model in (WeComSyncJob, WeComTransferItem, WeComExternalContact, WeComFollowRelationship,
                  Customer, Opportunity, Task, SampleRequest, SalesOrder, OrderDraft, Quote):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    raw.add(User(id=1, name='离职甲', username='handover-b1505', password_hash='x',
                 status='active', wecom_userid='handover-wx'))
    raw.add(User(id=2, name='接管乙', username='takeover-b1505', password_hash='x',
                 status='active', wecom_userid=None))
    raw.add(WeComExternalContact(id=1, external_userid='ext-b1505', name='隔离客户'))
    raw.add(WeComFollowRelationship(id=1, external_contact_id=1, wecom_userid='handover-wx', status='active'))
    raw.commit()

    result = await service.preview_transfer(
        SyncSessionAsAsync(raw), handover_user_id=1, takeover_user_id=2,
    )
    assert any('接管乙' in w and '绑定' in w for w in result['warnings'])


@pytest.mark.anyio
async def test_execution_skips_wecom_when_assignee_lacks_wecom(db_session, monkeypatch):
    from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.task.model import Task
    from app.modules.sample.model import SampleRequest
    from app.modules.order.model import SalesOrder, OrderDraft
    from app.modules.quote.model import Quote

    raw = db_session
    for model in (WeComSyncJob, WeComTransferItem, WeComExternalContact, WeComFollowRelationship,
                  Customer, Opportunity, Task, SampleRequest, SalesOrder, OrderDraft, Quote):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    raw.add(User(id=1, name='离职甲', username='exec-handover', password_hash='x',
                 status='active', wecom_userid='handover-wx'))
    raw.add(User(id=2, name='接管乙', username='exec-takeover', password_hash='x',
                 status='active', wecom_userid=None))
    raw.add(WeComExternalContact(id=1, external_userid='ext-exec', name='隔离客户'))
    raw.add(WeComFollowRelationship(id=1, external_contact_id=1, wecom_userid='handover-wx', status='active'))
    raw.commit()

    calls = []

    class NeverCalledAPI:
        async def transfer_customer(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(service.wecom_client, 'get_client', lambda: NeverCalledAPI())
    await service.transfer_relations(
        SyncSessionAsAsync(raw), user=make_user(1, roles=('admin',)),
        handover_user_id=1, takeover_user_id=2, transfer_wecom=True,
    )
    raw.expire_all()
    item = raw.scalars(select(WeComTransferItem).where(WeComTransferItem.kind == 'wecom_relation')).one()
    assert item.wecom_status == 'skipped'
    assert '未绑定企微' in (item.wecom_error or '')
    assert calls == []  # 外部调用未发出
