"""打样「制作依据」的四种状态（返修 R06）：历史未知与明确没选必须分得开。

**只在隔离库跑**：库名必须含 test（或 CI=true）。

## 这条修的是什么

打样单上的 `basis_files` 一列有两种"空"，含义正好相反：

- **NULL** ＝ 这份记录是在"制作依据"这个功能上线**之前**制完的 —— 「历史未知」，
  是"当时没记"，**不是**"当时没有"；
- **空列表** ＝ 登记的人**明确没选**任何依据 —— 一次真实的判断。

后端此前一律 `or []` 下发，把 NULL 抹成了空列表；前端于是只能显示"登记时未指定"，
**替老数据作了判断**（当年根本没得选），也把登记人做过的那个判断抹平了。

## 怎么验

造出四种状态，逐个读接口，看 `basis_state` 与中文标签是否分得开：

| 场景 | made_at | basis_files | 期望状态 |
|---|---|---|---|
| 还没登记制作完成 | NULL | NULL | `not_made` |
| 老数据（功能上线前制的） | 有 | NULL | `unknown` |
| 登记时明确没选 | 有 | `[]` | `none` |
| 指定了依据 | 有 | `[...]` | `specified` |

外加一条**走真实接口**的：对一张已批准的样本调 `POST /samples/{id}/made` 且不选
依据，库里应当写进**空列表**（而不是继续留 NULL）—— 写入端与读出端一起才成立。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_sample_basis_state.py
"""

import asyncio
import os
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKBASIS{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: object, expected: object = True) -> None:
    ok = condition == expected
    print(f'  {"OK  " if ok else "FAIL"} {label}：{condition!r}' + ("" if ok else f"（应为 {expected!r}）"))
    if not ok:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


async def seed_sample(made: bool, basis_json: str) -> int:
    """按给定的 (是否已登记制作, basis_files 的 JSON 字面量) 造一条打样单。"""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "insert into sample_requests "
                    "(customer_id, owner_id, status, requested_at, made_at, basis_files, remark) "
                    "values (1, 1, 'approved', now(), :made, cast(:basis as jsonb), :m) "
                    "returning id"
                ),
                {
                    "made": datetime.now(UTC) if made else None,
                    "basis": basis_json,
                    "m": MARKER,
                },
            )
        ).first()
        await session.commit()
    return int(row[0])


async def main() -> int:
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}, db
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db

    admin = login("admin", "admin123")

    try:
        print("=== 1. 四种状态各自说得清 ===")
        scenarios = [
            ("not_made", False, "null"),
            ("unknown", True, "null"),
            ("none", True, "[]"),
            ("specified", True, '[{"file_id": 1, "file_name": "图纸A.pdf", "checksum": "abc"}]'),
        ]
        seen: dict[str, object] = {}
        for name, made, basis in scenarios:
            sample_id = await seed_sample(made, basis)
            status, res = call("GET", f"/samples/{sample_id}", token=admin)
            assert status == 200, res
            data = res.get("data") or {}
            seen[name] = data.get("basis_state")
            check(f"{name}：basis_state", data.get("basis_state"), name)
            check_true(f"{name}：给了中文标签",
                       bool(data.get("basis_state_label")), str(data.get("basis_state_label")))
            if name in ("not_made", "unknown"):
                # 这两种是 NULL（没登记制作 / 老数据），**不是**空列表
                check(f"{name}：basis_files 是 NULL", data.get("basis_files"), None)
            elif name == "none":
                # 这一种是空列表 —— 与上面两种**必须分得开**，否则前端只能含糊成一句
                check(f"{name}：basis_files 是空列表（明确没选）",
                      data.get("basis_files"), [])
            else:
                check_true("specified：依据读得回来",
                           isinstance(data.get("basis_files"), list)
                           and len(data["basis_files"]) == 1,
                           str(data.get("basis_files")))

        print("=== 2. 「历史未知」和「明确没选」必须分得开（这条修的就是它）===")
        check_true("历史未知 ≠ 明确没选",
                   seen.get("unknown") != seen.get("none"),
                   f"unknown={seen.get('unknown')!r} none={seen.get('none')!r}")
        check_true("三种空值互不相同",
                   len({seen.get("not_made"), seen.get("unknown"), seen.get("none")}) == 3,
                   str(seen))
        check("老数据说成「历史未知」而不是替它断定", seen.get("unknown"), "unknown")

        print("=== 3. 登记时不选依据，要真的写进空列表（写入端）===")
        sample_id = await seed_sample(False, "null")  # 还没登记制作
        status, res = call("POST", f"/samples/{sample_id}/made", token=admin,
                           body={"remark": None})
        check("登记制作完成", status, 200)
        data = res.get("data") or {}
        check("明确没选 → 存成空列表（旧写法继续留 NULL）", data.get("basis_files"), [])
        check("并且状态是「明确未选」", data.get("basis_state"), "none")

        async with SessionLocal() as session:
            raw = (
                await session.execute(
                    text("select basis_files from sample_requests where id = :i"),
                    {"i": sample_id},
                )
            ).first()
        check_true("库里那行确实是空数组，不是 NULL",
                   raw is not None and raw[0] == [], str(raw[0] if raw else None))

    finally:
        async with SessionLocal() as session:
            await session.execute(
                text("delete from sample_requests where remark = :m"), {"m": MARKER}
            )
            await session.commit()
        print()
        print(f"（已清理夹具：备注为 {MARKER} 的打样单）")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("OK 打样制作依据：未制作 / 历史未知 / 明确未选 / 已指定，四种状态分得开")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
