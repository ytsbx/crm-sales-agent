"""Local copies must not reuse OA approvals whose CRM IDs happen to match."""
import pytest
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.modules.oa.client import approval_external_ref


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
