"""Local copies must not reuse OA approvals whose CRM IDs happen to match."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.modules.oa.client import approval_external_ref
from app.modules.oa.service import _looks_like_reused_ref


@pytest.mark.parametrize("kind", ["contract", "sample", "inquiry"])
def test_copy_namespace_preserves_default_keys_and_separates_data_sets(monkeypatch, kind):
    monkeypatch.setattr(settings, "oa_external_ref_namespace", "")
    original = approval_external_ref(kind, 1, 1)
    assert original == f"crm-{kind}:1:1"
    monkeypatch.setattr(settings, "oa_external_ref_namespace", "local-copy-20261116")
    copied = approval_external_ref(kind, 1, 1)
    assert copied == f"local-copy-20261116:{original}"
    assert approval_external_ref(kind, 1, 1) == copied
    assert approval_external_ref(kind, 1, 2) != copied


@pytest.mark.parametrize("namespace", ["a" * 49, "invalid:prefix", "with spaces"])
def test_namespace_rejects_invalid_or_oversized_prefix(namespace):
    with pytest.raises(ValidationError):
        Settings(oa_external_ref_namespace=namespace)


def _row(created_at):
    return SimpleNamespace(created_at=created_at)


def test_reused_ref_detected_when_oa_receipt_is_much_older():
    now = datetime.now(UTC)
    summary = {"submitted_at": (now - timedelta(days=2)).isoformat()}
    assert _looks_like_reused_ref(summary, _row(now))


def test_recent_receipt_is_not_treated_as_reused():
    now = datetime.now(UTC)
    summary = {"submitted_at": (now - timedelta(minutes=10)).isoformat()}
    assert not _looks_like_reused_ref(summary, _row(now))


def test_missing_or_invalid_receipt_time_is_ignored():
    now = datetime.now(UTC)
    assert not _looks_like_reused_ref({}, _row(now))
    assert not _looks_like_reused_ref({"submitted_at": "not-a-time"}, _row(now))


def test_naive_receipt_and_row_times_are_treated_as_utc():
    created = datetime(2026, 11, 25, 10, 0)
    old = datetime(2026, 11, 20, 10, 0)
    assert _looks_like_reused_ref({"submitted_at": old.isoformat()}, _row(created))
