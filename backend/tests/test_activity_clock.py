"""旧的无时区时间与 PostgreSQL 有时区值不能使扫描整体失败。"""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.modules.settings.service import _last_active_at


def test_mixed_clock_timezone_uses_latest_fact():
    customer = SimpleNamespace(last_followup_at=datetime(2026, 10, 1),
                               last_progress_at=datetime(2026, 10, 2, tzinfo=UTC),
                               created_at=datetime(2026, 9, 1))
    assert _last_active_at(customer) == datetime(2026, 10, 2, tzinfo=UTC)


def test_naive_creation_fallback_normalized():
    customer = SimpleNamespace(last_followup_at=None, last_progress_at=None,
                               created_at=datetime(2026, 9, 1))
    assert _last_active_at(customer) == datetime(2026, 9, 1, tzinfo=UTC)
