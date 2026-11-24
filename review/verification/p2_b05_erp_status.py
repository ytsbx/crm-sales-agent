"""P2/B05-06：ERP 返回多单时，适配器取 orders[0]，不校验 o_id 是否等于查询键。"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.erp.adapter import JushuitanAdapter  # noqa: E402


async def main():
    adapter = JushuitanAdapter()
    async def fake_call(path, params, capability=None):
        return {"data": {"orders": [
            {"o_id": "OTHER-ORDER", "status": "Sent", "send_date": "2026-11-20"},
            {"o_id": "WANTED-ORDER", "status": "Cancelled"},
        ]}}
    adapter._call = fake_call  # type: ignore[method-assign]
    got = await adapter.fetch_order_status("WANTED-ORDER")
    print(f"[B05-06] 查 WANTED-ORDER，返回 status={got['status']} raw_status={got['raw_status']} shipped_at={got['shipped_at']}")


if __name__ == "__main__":
    asyncio.run(main())
