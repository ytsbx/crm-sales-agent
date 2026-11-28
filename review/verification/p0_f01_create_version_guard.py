"""F01-01 当前代码实测：已有有效订单后，再建版本应被 409 拒绝。

运行：backend/.venv/bin/python review/verification/p0_f01_create_version_guard.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402

from app.core.errors import AppError  # noqa: E402
from app.modules.order import service as order_service  # noqa: E402
from app.modules.quote import service as quote_service  # noqa: E402
from p0_quote_duplicate_convert import prepare  # noqa: E402


async def main():
    engine = make_engine()
    raw, quote, version1, _version2 = prepare(engine)
    session = SyncSessionAsAsync(raw)
    with patch.object(order_service, "generate_order_no", AsyncMock(return_value="P0-SO-1")), \
         patch("app.modules.followup.service.record_and_notify", AsyncMock()):
        await order_service.create_order_from_quote(session, version=version1, user_id=101)
        await session.flush()
        user = make_user(101, permissions=("quote:manage",))
        try:
            await quote_service.create_version(session, quote=quote, user=user)
        except AppError as exc:
            print(f"create_version -> {exc.http_status} {exc.code} {exc.message}")
            result = "BLOCKED (PASS)" if exc.http_status == 409 else "FAILED"
        else:
            print("create_version -> UNEXPECTED SUCCESS")
            result = "FAILED"
    raw.close()
    engine.dispose()
    print("F01-01 VERSION RESULT:", result)
    sys.exit(0 if result.startswith("BLOCKED") else 1)


asyncio.run(main())
