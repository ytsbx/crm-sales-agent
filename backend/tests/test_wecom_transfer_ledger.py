"""交接首份底账必须足够让中断任务恢复；仅独立内存SQLite。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.user.model import User
from app.modules.wecom import service
from app.modules.wecom.model import TRANSFER_KIND_LABEL, WeComSyncJob, WeComTransferItem
from conftest import SyncSessionAsAsync


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
