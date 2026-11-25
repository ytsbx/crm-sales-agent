"""B13-06：同一份客户导入文件只能执行一次。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.customer.io_router import _reject_reimported_file
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def _open():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AuditLog.__table__.create(engine)
    raw = Session(engine)
    return engine, raw


def _import_audit(sha: str, *, business_type: str = "customer", operator_id: int | None = None):
    return AuditLog(
        operator_id=operator_id,
        action="import",
        business_type=business_type,
        after_data={"file_sha256": sha},
        created_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_same_file_is_rejected_after_a_previous_import():
    engine, raw = _open()
    raw.add(_import_audit("a" * 64))
    raw.commit()
    with pytest.raises(AppError) as caught:
        await _reject_reimported_file(SyncSessionAsAsync(raw), "a" * 64)
    assert caught.value.http_status == 409
    assert "不能重复导入" in caught.value.message
    raw.close()
    engine.dispose()


@pytest.mark.anyio
async def test_new_file_or_other_module_is_not_blocked():
    engine, raw = _open()
    raw.add(_import_audit("b" * 64))
    raw.add(_import_audit("a" * 64, business_type="product"))
    raw.commit()
    # 不同文件；以及同一份内容但属于别的模块（产品导入）都不算客户重复导入
    await _reject_reimported_file(SyncSessionAsAsync(raw), "a" * 64)
    raw.close()
    engine.dispose()
