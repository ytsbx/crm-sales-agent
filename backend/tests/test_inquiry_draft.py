"""统一表单草稿：允许缺少审批必填项，保存不触发钉钉。"""
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from starlette.requests import Request
from app.modules.inquiry import router as routes
from app.modules.inquiry.schema import CustomInquiryCreate, CustomInquiryUpdate
from app.modules.dingtalk import router as oa


def test_draft_identity_and_attachment_limits():
    draft={'fields':{'optional':'说明'},'quote_owner_user_id':'q','attachment_file_ids':[5]}
    assert CustomInquiryCreate(title='产品',oa_draft=draft).oa_draft.fields==draft['fields']
    assert CustomInquiryUpdate(oa_draft=draft).model_dump(exclude_unset=True)['oa_draft']==draft
    for bad in ({**draft,'originator_user_id':'forged'},{**draft,'attachment_file_ids':[-1]}, {**draft,'attachment_file_ids':list(range(1,12))}):
        with pytest.raises(ValidationError):CustomInquiryCreate(title='产品',oa_draft=bad)


@pytest.mark.anyio
async def test_create_saves_draft_without_external_submission(monkeypatch):
    added=[]
    session=Obj(add=lambda row:added.append(row),flush=AsyncMock(),commit=AsyncMock())
    monkeypatch.setattr(routes.svc,'validate_links',AsyncMock(return_value=None))
    monkeypatch.setattr(routes.svc,'generate_inquiry_no',AsyncMock(return_value='INQ-TEST'))
    monkeypatch.setattr(routes.svc,'serialize',lambda row:{'title':row.title,'extra':row.extra})
    monkeypatch.setattr(routes,'write_audit',AsyncMock())
    send=AsyncMock(side_effect=AssertionError('saving draft must not send'))
    monkeypatch.setattr(oa.svc,'create_inquiry_instance',send)
    payload=CustomInquiryCreate(title='产品',oa_draft={'fields':{'note':'未完成'},'attachment_file_ids':[]})
    result=await routes.create_inquiry(payload,Request({'type':'http','headers':[]}),Obj(id=1),session)
    assert added[0].extra['oa_draft']['fields']=={'note':'未完成'}
    assert result['data']['extra']['oa_draft']['fields']['note']=='未完成'
    send.assert_not_awaited()


@pytest.mark.anyio
async def test_new_template_options_do_not_create_inquiry(monkeypatch):
    # Endpoint has no inquiry ID: only read template/config, never create a provisional business record.
    monkeypatch.setattr(oa,'_submission_options',AsyncMock(return_value={'data':{'fields':[]}}))
    session=Obj(commit=AsyncMock(),add=Mock())
    result=await oa.new_inquiry_submission_options(user=Obj(id=1),session=session)
    assert result['data']['fields']==[]
    session.commit.assert_not_awaited();session.add.assert_not_called()

@pytest.mark.anyio
async def test_update_draft_preserves_source_and_history_guard(monkeypatch):
    row=Obj(id=7,root_id=None,deleted_at=None,superseded_at=None,extra={'insight_id':99})
    session=Obj(execute=AsyncMock(),refresh=AsyncMock(),flush=AsyncMock(),commit=AsyncMock())
    monkeypatch.setattr(routes.svc,'get_visible_or_404',AsyncMock(return_value=row))
    monkeypatch.setattr(routes.svc,'serialize',lambda value:{'extra':value.extra})
    monkeypatch.setattr(routes,'write_audit',AsyncMock())
    payload=CustomInquiryUpdate(oa_draft={'fields':{'note':'保留'},'attachment_file_ids':[1]})
    result=await routes.update_inquiry(7,payload,Request({'type':'http','headers':[]}),Obj(id=1),session)
    assert result['data']['extra']['insight_id']==99
    assert row.extra['oa_draft']['fields']['note']=='保留'
    row.superseded_at='historical'
    from app.core.errors import AppError
    with pytest.raises(AppError):await routes.update_inquiry(7,payload,Request({'type':'http','headers':[]}),Obj(id=1),session)
