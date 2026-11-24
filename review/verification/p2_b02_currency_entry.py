"""P2/B02-07 入口可达性：核价接口先把非 CNY 拒绝，缺汇率分支只剩历史数据路径。"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from conftest import SyncSessionAsAsync, make_engine  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.trade_mode import ensure_currency_allowed  # noqa: E402


async def main():
    engine = make_engine()
    session = SyncSessionAsAsync(__import__("sqlalchemy.orm", fromlist=["Session"]).Session(engine))
    for currency in ("CNY", "USD", "EUR"):
        try:
            allowed = await ensure_currency_allowed(session, currency, label="核价币种")
            print(f"[B02-07] currency={currency} -> 允许 {allowed}")
        except AppError as exc:
            print(f"[B02-07] currency={currency} -> 拒绝 code={exc.code} message={exc.message}")
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
