"""历史联系补核必须是已发生事实，保留历史值及清空语义。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.modules.customer.schema import CustomerUpdate


def test_future_contact_is_rejected():
    with pytest.raises(ValidationError, match="不能在未来"):
        CustomerUpdate(last_followup_at=datetime.now(UTC) + timedelta(days=1))


def test_historical_contact_and_timezone_equivalence_are_preserved():
    past = datetime.now(UTC) - timedelta(days=60)
    shanghai = past.astimezone(timezone(timedelta(hours=8)))
    assert CustomerUpdate(last_followup_at=shanghai).last_followup_at == past
    assert CustomerUpdate(last_followup_at=past.replace(tzinfo=None)).last_followup_at == past


def test_contact_clear_and_omission_have_different_patch_meaning():
    assert CustomerUpdate(last_followup_at=None).model_dump(exclude_unset=True) == {
        "last_followup_at": None,
    }
    assert CustomerUpdate().model_dump(exclude_unset=True) == {}
