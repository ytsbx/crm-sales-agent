"""批量分配原因沿用单条入口与数据库同一长度，不能静默截断。"""
import pytest
from pydantic import ValidationError
from app.modules.lead.schema import LeadAssign, LeadBatchAssign


@pytest.mark.parametrize('reason', [None, '', '测'*255, 'a'*255])
def test_batch_reason_accepts_existing_limits(reason):
    batch=LeadBatchAssign(lead_ids=[1,2],owner_id=3,reason=reason)
    single=LeadAssign(owner_id=3,reason=reason)
    assert batch.reason==single.reason==reason


@pytest.mark.parametrize('reason', ['测'*256,'a'*256])
def test_batch_reason_rejects_overlong_text(reason):
    for model,fields in [(LeadAssign,{'owner_id':3}), (LeadBatchAssign,{'lead_ids':[1,2],'owner_id':3})]:
        with pytest.raises(ValidationError) as caught:
            model(**fields,reason=reason)
        assert caught.value.errors()[0]['loc']==('reason',)
