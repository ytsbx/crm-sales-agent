"""K4：订单草稿作废（软作废，不删明细/审计/文件）。"""
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.order import drafts
from app.modules.order import drafts_router
from app.modules.order.model import OrderDraft, OrderDraftItem
from conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


def engine_with_tables():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (OrderDraft, OrderDraftItem, AuditLog):
        model.__table__.create(engine)
    return engine


def draft(**changes):
    values = dict(
        id=1, customer_id=1, owner_id=1, created_by=1, status='draft',
        source_context={'type': 'quote', 'id': 1, 'no': 'Q1', 'version': 1},
        currency='CNY', request_key='key-1', request_hash='h', revision=1,
        created_at=datetime.now(UTC),
    )
    return OrderDraft(**(values | changes))


@pytest.mark.anyio
async def test_cancel_soft_marks_and_keeps_details():
    engine = engine_with_tables()
    try:
        with Session(engine) as raw:
            row = draft()
            raw.add(row)
            raw.add(OrderDraftItem(draft_id=1, source_snapshot={'source_item_id': 1},
                                   name='产品', quantity=1))
            raw.commit()
            actor = make_user(1, permissions=('order:manage',), data_scope='all')
            await drafts.cancel(SyncSessionAsAsync(raw), actor, 1, '客户取消')
            assert row.status == 'cancelled'
            assert row.source_context['_cancel']['reason'] == '客户取消'
            # 明细保留
            assert raw.query(OrderDraftItem).filter_by(draft_id=1).count() == 1
            audit = raw.query(AuditLog).filter_by(action='cancel', business_id=1).one()
            assert audit.after_data['status'] == 'cancelled'
            # 作废后不能再编辑或转单
            with pytest.raises(AppError):
                drafts.require_editable(row, row.revision)
            with pytest.raises(AppError):
                await drafts.cancel(SyncSessionAsAsync(raw), actor, 1, '再作废')
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_converted_draft_cannot_be_cancelled():
    engine = engine_with_tables()
    try:
        with Session(engine) as raw:
            raw.add(draft(id=2, status='converted', order_id=99))
            raw.commit()
            actor = make_user(1, permissions=('order:manage',), data_scope='all')
            with pytest.raises(AppError) as caught:
                await drafts.cancel(SyncSessionAsAsync(raw), actor, 2, '不做了')
            assert caught.value.http_status == 422
            assert '不能作废' in caught.value.message
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_cancelled_key_returns_explicit_conflict():
    engine = engine_with_tables()
    try:
        with Session(engine) as raw:
            payload = SimpleNamespace(request_key='old-key')
            payload.model_dump = lambda **_: {'quote_version_id': 1, 'items': []}
            digest = hashlib.sha256(json.dumps(
                payload.model_dump(mode='json', exclude={'request_key'}),
                sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest()
            raw.add(draft(id=3, status='cancelled', request_key='old-key', request_hash=digest))
            raw.commit()
            actor = make_user(1, permissions=('order:manage',), data_scope='all')
            with pytest.raises(AppError) as caught:
                await drafts.create(SyncSessionAsAsync(raw), actor, payload)
            assert caught.value.http_status == 409
            assert '已作废' in caught.value.message
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_list_filters_by_status():
    engine = engine_with_tables()
    try:
        with Session(engine) as raw:
            raw.add_all([draft(id=4), draft(id=5, status='cancelled', request_key='key-5')])
            raw.commit()
            user = make_user(1, permissions=('order:view',), data_scope='all')
            result = await drafts_router.listing(
                status='cancelled', page=1, page_size=20,
                user=user, session=SyncSessionAsAsync(raw),
            )
            assert [row['id'] for row in result['data']['items']] == [5]
    finally:
        engine.dispose()
