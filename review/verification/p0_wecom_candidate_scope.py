"""P0/F10-01 隔离 SQLite 复现脚本：只有 wecom:view 的人能否看到他人客户。

运行：backend/.venv/bin/python review/verification/p0_wecom_candidate_scope.py
"""
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine  # noqa: E402
from app.modules.customer.model import Contact, Customer  # noqa: E402
from app.modules.settings.model import SystemSetting  # noqa: E402
from app.modules.wecom import service as wecom_service  # noqa: E402
from app.modules.wecom.model import WeComExternalContact  # noqa: E402

NOW = datetime.now(UTC)


async def main():
    engine = make_engine()
    for table in (Contact, SystemSetting, WeComExternalContact):
        table.__table__.create(engine, checkfirst=True)

    with Session(engine, expire_on_commit=False) as raw:
        # 两个客户都不属于调用者（owner_id=999 是别人），且带等级/地区/负责人。
        raw.add_all([
            Customer(id=1, name="宁波海辰进出口有限公司", owner_id=999,
                     level="A", region="华东", pool_status=None),
            Customer(id=2, name="宁波海辰贸易有限公司", owner_id=888,
                     level="B", region="华南", pool_status=None),
        ])
        raw.add(WeComExternalContact(
            id=1, external_userid="wm-p0", name="张总",
            corp_name="宁波海辰进出口有限公司", normalize_status="pending",
            last_sync_at=NOW,
        ))
        raw.commit()

        session = SyncSessionAsAsync(raw)
        result = await wecom_service.unbound_candidates(session, contact_id=1)
        print("[调用者权限] 只有 wecom:view（路由依赖 require_permission('wecom:view')）")
        print(f"[返回候选数] {len(result['candidates'])}")
        for item in result["candidates"]:
            print(f"[泄露字段] id={item['id']} name={item['name']} level={item['level']} "
                  f"region={item['region']} owner_id={item['owner_id']} score={item['score']}")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
