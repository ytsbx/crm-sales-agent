"""只启用 OA 同步时，不顺带启动回收、跟单、通知等其它任务。"""
from unittest.mock import Mock

import pytest

from app.core import scheduler as module


@pytest.mark.parametrize('general,oa,expected',[
    (False,False,set()),
    (False,True,{'oa_sync'}),
    (True,False,{'public_pool_recycle','auto_task_rules','milestone_overdue','notification_retry','notification_digest','oa_sync'}),
    (True,True,{'public_pool_recycle','auto_task_rules','milestone_overdue','notification_retry','notification_digest','oa_sync'}),
])
def test_only_requested_jobs_are_started(monkeypatch,general,oa,expected):
    fake=Mock()
    monkeypatch.setattr(module,'scheduler',fake)
    monkeypatch.setattr(module.settings,'scheduler_enabled',general)
    monkeypatch.setattr(module.settings,'scheduler_oa_sync_enabled',oa)
    module.start_scheduler()
    ids=[call.kwargs['id'] for call in fake.add_job.call_args_list]
    assert set(ids)==expected
    assert len(ids)==len(set(ids))
    assert fake.start.call_count==bool(expected)
    if expected:
        call=next(call for call in fake.add_job.call_args_list if call.kwargs['id']=='oa_sync')
        assert call.kwargs['minutes']==module.settings.scheduler_oa_sync_minutes
        assert call.kwargs['max_instances']==1


@pytest.mark.parametrize('push_off,login_off',[(False,True),(True,False),(False,False)])
def test_directory_available_for_approval_or_login(monkeypatch,push_off,login_off):
    from app.modules.dingtalk.client import DingTalkClient
    monkeypatch.setattr(module.settings,'dingtalk_push_off',push_off)
    monkeypatch.setattr(module.settings,'dingtalk_login_off',login_off)
    monkeypatch.setattr(module.settings,'dingtalk_app_key','test-key')
    monkeypatch.setattr(module.settings,'dingtalk_app_secret','test-secret')
    client=DingTalkClient()
    client._require_directory()
    if login_off:
        from app.modules.dingtalk.client import DingTalkDisabled
        with pytest.raises(DingTalkDisabled):client._require_login()
