"""自研 OA 打样：草稿保存、快照编辑识别、提交分支。

旧钉钉打样审批路径已于 2026-11-25 删除；这里只守仍在使用的自研 OA 行为，
外部调用全部用替身，不连库、不碰钉钉/OA。
"""
from decimal import Decimal
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.sample import dingtalk as routes
from app.modules.sample.model import SampleRequest


def request():
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


@pytest.fixture
def setup(monkeypatch):
    sample = SampleRequest(
        id=7, customer_id=2, owner_id=1, status='pending', version=1,
        source_context={}, made_at=None,
    )
    session = Obj(
        get=AsyncMock(return_value=None), flush=AsyncMock(), commit=AsyncMock(),
        execute=AsyncMock(return_value=Obj(all=lambda: [])),
    )
    user = Obj(id=1, name='业务员甲', has=lambda _: True)
    monkeypatch.setattr(routes.service, 'get_visible_or_404', AsyncMock(return_value=sample))
    monkeypatch.setattr(routes, 'inquiry_candidates', AsyncMock(return_value=[{'id': 8, 'label': '来源审批'}]))
    monkeypatch.setattr(routes, 'write_audit', AsyncMock())
    monkeypatch.setattr(routes.oa, 'get_by_business_key', AsyncMock(return_value=None))
    monkeypatch.setattr(routes.oa, 'serialize', lambda row: {'status': row.status, 'instance_id': row.instance_id})
    monkeypatch.setattr(routes, 'template', AsyncMock(return_value=(
        {'configured': True, 'enabled': True, 'fields': [
            {'id': 'DDDateField_DUE', 'label': '要求完成日期', 'type': 'DDDateField',
             'required': True, 'system': False, 'options': [], 'format': 'yyyy-MM-dd'},
        ]}, None)))
    return sample, session, user


@pytest.mark.anyio
async def test_draft_does_not_send(setup):
    sample, session, user = setup
    await routes.save_draft(7, routes.Draft(mode='standalone', fields={'note': '草稿'}), request(), user, session)
    assert routes.metadata(sample)['fields'] == {'note': '草稿'}
    assert not routes.metadata(sample).get('submitted')


@pytest.mark.anyio
async def test_native_draft_persists_fields_used_by_the_excel(setup, monkeypatch):
    """保存草稿后马上下载《打样申请单》，打样类型/部门/积分不能还是空的。"""
    sample, session, user = setup
    monkeypatch.setattr(routes, 'template', AsyncMock(return_value=(
        {'configured': True, 'enabled': True, 'native_form': True, 'fields': [
            {'id': 'sample_type', 'label': '打样类型', 'type': 'DDSelectField',
             'required': True, 'system': False, 'options': []},
            {'id': 'product_category', 'label': '打样产品大类', 'type': 'DDSelectField',
             'required': True, 'system': False, 'options': []},
            {'id': 'maker_unit', 'label': '样品制作单位', 'type': 'DDSelectField',
             'required': True, 'system': False, 'options': []},
            {'id': 'target_date', 'label': '要求完成日期', 'type': 'DDDateField',
             'required': True, 'system': False, 'options': []},
            {'id': 'score', 'label': '本次打样积分', 'type': 'NumberField',
             'required': True, 'system': False, 'options': []},
        ]}, None)))
    await routes.save_draft(7, routes.Draft(mode='standalone', fields={
        'sample_type': '销售客户打样', 'product_category': '高频类产品',
        'maker_unit': '义乌打样部', 'score': '10',
    }), request(), user, session)
    assert sample.sample_type == '销售客户打样'
    assert sample.product_category == '高频类产品'
    assert sample.maker_unit == '义乌打样部'
    assert sample.estimated_hours == Decimal('1.00')
    assert sample.estimated_hours * 10 == Decimal('10')


@pytest.mark.anyio
async def test_submit_without_self_oa_template_returns_config_error(monkeypatch):
    monkeypatch.setattr(routes, 'self_oa_available', lambda: False)
    result, code = await routes.template(Obj())
    assert result['configured'] is False
    assert result['enabled'] is False
    assert '同序 OA' in result['template_error']
    assert code is None


def test_backend_computed_fields_do_not_count_as_snapshot_edits():
    """后端每次提交现算的字段不在快照里，不能因此报"改写了快照"。"""
    from app.modules.sample.dingtalk import snapshot_edit_detail

    snapshot = {'DDSelectField_A': '设计研发内部打样', '_estimated_hours': '1'}
    incoming = {**snapshot,
                'InnerContactField_X': '021003563141679555',
                'DepartmentField_Y': '1115706176',
                'DDAttachment_Z': '[{"fileId":"1"}]'}
    assert snapshot_edit_detail(snapshot, incoming) == ''


def test_real_edits_are_still_reported_with_details():
    from app.modules.sample.dingtalk import snapshot_edit_detail

    snapshot = {'DDSelectField_A': '设计研发内部打样'}
    detail = snapshot_edit_detail(snapshot, {**snapshot, 'DDSelectField_A': '销售客户打样'})
    assert detail
    assert 'DDSelectField_A' in detail and '设计研发内部打样' in detail


def test_removing_a_field_counts_as_an_edit():
    from app.modules.sample.dingtalk import snapshot_edit_detail

    assert snapshot_edit_detail({'A': 'x'}, {'A': ''}) != ''


@pytest.mark.parametrize('submitted', [False, True])
def test_approval_submission_locks_crm_edits(submitted):
    sample = Obj(source_context={'dingtalk_oa': {'mode': 'standalone', 'submitted': submitted}})
    if submitted:
        with pytest.raises(AppError):
            routes.ensure_editable(sample)
    else:
        routes.ensure_editable(sample)
    routes.ensure_editable(Obj(source_context=None))


@pytest.mark.anyio
async def test_submit_uses_self_oa_when_configured(setup, monkeypatch):
    sample, session, user = setup
    monkeypatch.setattr(routes.oa_client, 'configured', lambda: True)
    monkeypatch.setattr(routes.settings, 'oa_sample_template_id', 'tpl-oa')
    row = Obj(status='pending', instance_id='oa-sample-1')
    start = AsyncMock(return_value=(row, '已发起 OA 打样审批'))
    monkeypatch.setattr(routes.oa_sample, 'start_sample_approval', start)
    result = await routes.submit(
        7, routes.Draft(mode='standalone', fields={'sample_type': '销售客户打样'}),
        request(), user, session,
    )
    assert result['data']['status'] == 'pending'
    start.assert_awaited_once()


@pytest.mark.anyio
async def test_failed_retry_can_edit_fields_and_updates_snapshot(setup, monkeypatch):
    """B11-10：外部明确拒绝（确定没建单）后，允许改字段重试并更新快照。

    此前"重试"被锁成一字不改：提示说"可以修正后重试"，真改了字段却 409，
    用户只能删单重建。现在 failed/not_sent/skipped 允许改，差异进审计。
    """
    from datetime import date

    sample, session, user = setup
    sample.source_context = {'dingtalk_oa': {
        'mode': 'standalone', 'submitted': True,
        'fields': {'sample_type': '销售客户打样', '_estimated_hours': '1'},
    }}
    monkeypatch.setattr(routes, 'self_oa_available', lambda: True)
    monkeypatch.setattr(routes.oa, 'get_by_business_key', AsyncMock(
        return_value=Obj(status='failed', provider='self_oa')))
    start = AsyncMock(return_value=(Obj(status='failed', instance_id=None), '已重新发起'))
    monkeypatch.setattr(routes.oa_sample, 'start_sample_approval', start)
    audit = AsyncMock()
    monkeypatch.setattr(routes, 'write_audit', audit)

    await routes.submit(7, routes.Draft(mode='standalone', fields={
        'sample_type': '外协厂打样',
        '_estimated_hours': '2',
        'DDDateField_DUE': '2026-12-01',
    }), request(), user, session)

    start.assert_awaited_once()
    meta = routes.metadata(sample)
    assert meta['submitted'] is True
    assert meta['fields']['sample_type'] == '外协厂打样'
    assert sample.target_completion_date == date(2026, 12, 1)
    after = audit.await_args.kwargs['after']
    assert '销售客户打样' in after['retry_edits']
    assert '外协厂打样' in after['retry_edits']


@pytest.mark.anyio
async def test_needs_review_retry_still_refused(setup, monkeypatch):
    """结果未知（可能已建单）不开放改字段重试：必须先到 OA 核对。"""
    sample, session, user = setup
    sample.source_context = {'dingtalk_oa': {
        'mode': 'standalone', 'submitted': True, 'fields': {'sample_type': '销售客户打样'},
    }}
    monkeypatch.setattr(routes, 'self_oa_available', lambda: True)
    monkeypatch.setattr(routes.oa, 'get_by_business_key', AsyncMock(
        return_value=Obj(status='needs_review', provider='self_oa')))
    from app.modules.oa import service as oa_service
    monkeypatch.setattr(oa_service, 'review_stuck_submission', AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(routes.oa_sample, 'start_sample_approval', start)

    with pytest.raises(AppError) as caught:
        await routes.submit(7, routes.Draft(mode='standalone', fields={
            'sample_type': '外协厂打样'}), request(), user, session)
    assert '核对' in caught.value.message
    start.assert_not_awaited()
