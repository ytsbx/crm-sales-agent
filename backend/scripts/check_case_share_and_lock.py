"""案例两项 P1 修复的回归：证据标签脱敏（R01）+ 编辑加锁（R02）。

**只在隔离库跑**：库名必须含 test（或 CI=true）。

## 覆盖

**R01（证据标签泄露）**：案例标题 / 正文 / 审核意见早就脱敏了，但证据列表里的
`label` 一直原样下发 —— 作者在标签里写「某某集团 138… 成本 12 元」，
分享版读者照样看得见。这里造一条案例、挂一条含「客户全称 + 手机号」的证据，
分别用作者视角与普通读者视角各读一次：作者应看到原文，
普通读者应看到 `〔手机号〕`、且看不到客户全称。

**R02（编辑与审核并发）**：编辑接口此前先读状态、随后写，**全程没有行锁** ——
编辑请求停留期间主管审核发布，那笔旧编辑仍会覆盖已发布正文
（`_apply_update` 只写叙述列、不写 status，所以"已发布"这个状态还在，正文却换了）。

用**持锁量耗时**的确定性做法验（不靠"并发恰好撞上"）：另起线程在独立事务里
锁住案例行、把它改成 published，主线程此刻发 PATCH：
  - 修复前：PATCH 几乎立刻返回（没被挡住），状态还是 pending_review → 200，正文被改掉；
  - 修复后：PATCH 被锁挡住 ≈ 持锁时长，拿到锁后**锁内重读**发现已发布 → 422，正文没动。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_case_share_and_lock.py
"""

import asyncio
import os
import threading
import time
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKSHARE{int(time.time())}"
HOLD_SECONDS = 3.0
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


def _hold_and_publish(case_id: int, hold_seconds: float, ready, result: dict) -> None:
    """在独立线程 + 独立引擎里：锁住这条案例 → 等一会儿 → 把它改成已发布 → 提交。

    ⚠️ 必须用**独立引擎**（`poolclass=NullPool`）：应用那个全局 engine 是在主事件
    循环里建的，拿到子线程的 `asyncio.run()` 里用会挂在"连接绑定到另一个 loop"，
    而且异常是在子线程里抛的、**完全静默**（这个坑本项目踩过，会得出相反结论）。
    子线程的异常也必须自己 catch，否则 `join()` 一个字都不说。
    """

    async def _run() -> None:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("select id from sales_cases where id = :i for update"), {"i": case_id}
                )
                ready.set()
                await asyncio.sleep(hold_seconds)
                await conn.execute(
                    text("update sales_cases set status = 'published' where id = :i"),
                    {"i": case_id},
                )
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as error:  # noqa: BLE001 - 子线程异常必须自己收下
        result["error"] = f"{type(error).__name__}: {error}"


async def main():
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db

    admin = login("admin", "admin123")        # 作者 + 审核人（管理员）
    zhangsan = login("zhangsan", "123456")    # 非作者、非主管 → **分享版**读者

    from app.modules.customer.model import Customer

    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).limit(1))).scalars().first()
    assert customer is not None, "隔离库要先跑 scripts/seed.py"
    print(f"夹具客户：{customer.name}（id={customer.id}）")

    try:
        # ---------------- R01：证据标签脱敏 ----------------
        print()
        print("=== R01. 证据标签在分享版必须脱敏 ===")
        status, res = call("POST", "/cases", token=admin, body={
            "title": f"{MARKER} 证据标签脱敏",
            "customer_id": customer.id,
            "customer_label": "某机械厂",
            "key_actions": "按客户预算倒推报价",
            "lessons": "先确认产能再承诺交期",
        })
        assert status == 200, res
        case_id = res["data"]["id"]

        # 直接落一行证据：本项验的是**输出脱敏**，不是输入校验
        # （输入校验另有断言覆盖，这里刻意用最短路径造出"库里已有一条带敏感信息的标签"）
        raw_label = f"{customer.name} 13800138000 成本12元"
        async with SessionLocal() as session:
            await session.execute(
                text(
                    "insert into case_evidences (case_id, kind, business_id, label)"
                    " values (:c, 'quote', :b, :l)"
                ),
                {"c": case_id, "b": 999001, "l": raw_label},
            )
            await session.commit()

        status, res = call("POST", f"/cases/{case_id}/submit", token=admin)
        check("提交审核", status, 200)
        status, res = call("POST", f"/cases/{case_id}/review", token=admin,
                           body={"approve": True, "note": "通过"})
        check("审核通过（案例发布）", status, 200)

        status, res = call("GET", f"/cases/{case_id}", token=admin)
        check("作者能读到详情", status, 200)
        admin_ev = (res["data"].get("evidences") or [])
        check("作者视角：证据下发 1 条", len(admin_ev), 1)
        if admin_ev:
            admin_label = admin_ev[0].get("label") or ""
            check_true("作者视角：标签是原文（含手机号）", "13800138000" in admin_label, admin_label)

        status, res = call("GET", f"/cases/{case_id}", token=zhangsan)
        check("普通读者能读到详情（已发布，人尽可读）", status, 200)
        zs_ev = (res["data"].get("evidences") or [])
        check_true(
            "普通读者视角：证据仍下发（不是被单据权限挡掉的）",
            len(zs_ev) == 1,
            f"evidences={zs_ev} hidden={res['data'].get('hidden_evidence')}",
        )
        if zs_ev:
            zs_label = zs_ev[0].get("label") or ""
            print(f"     分享版看到的标签：{zs_label!r}")
            check_true("分享版：手机号已抹掉", "13800138000" not in zs_label, zs_label)
            check_true("分享版：留了可解释的记号", "〔手机号〕" in zs_label, zs_label)
            check_true(
                "分享版：客户全称已换成代称",
                customer.name not in zs_label,
                zs_label,
            )

        # ---------------- R02：编辑与审核并发 ----------------
        print()
        print("=== R02. 并发审核时，旧编辑不得覆盖已发布正文 ===")
        status, res = call("POST", "/cases", token=admin, body={
            "title": f"{MARKER} 并发编辑",
            "customer_id": customer.id,
            "customer_label": "某机械厂",
            "key_actions": "占位",
            "lessons": "占位",
        })
        assert status == 200, res
        case2 = res["data"]["id"]
        status, res = call("POST", f"/cases/{case2}/submit", token=admin)
        check("第二条案例已提交待审", status, 200)

        ready = threading.Event()
        holder: dict = {}
        thread = threading.Thread(
            target=_hold_and_publish,
            args=(case2, HOLD_SECONDS, ready, holder),
            daemon=True,
        )
        thread.start()
        got_lock = ready.wait(timeout=10)
        check_true("持锁线程已锁住案例行", got_lock)

        started = time.monotonic()
        status, res = call("PATCH", f"/cases/{case2}", token=admin,
                           body={"result": "被并发编辑覆盖的正文"})
        elapsed = time.monotonic() - started
        thread.join(timeout=10)

        check_true("持锁线程没报错", "error" not in holder, holder.get("error", ""))
        # 这一条是**装置自检**，不是修复的判据：数据库层的行锁本来就挡住 UPDATE，
        # 应用层加不加锁都一样慢 —— 反向验证时它照样绿（实测踩到，属"假绿"断言）。
        check_true(
            "装置自检：持锁期间写操作确实被挡住（证明这套实验有效）",
            elapsed >= HOLD_SECONDS * 0.66,
            f"耗时 {elapsed:.2f}s，持锁 {HOLD_SECONDS}s",
        )
        # 下面两条才是**能区分修复与否**的判据：
        # 旧写法（无锁）→ 200 且正文被改掉；加锁 + 锁内重读 → 422 且正文原样。
        check("拿到锁后锁内重读发现已发布 → 拒绝（旧写法这里返回 200）", status, 422)

        async with SessionLocal() as session:
            row = (
                await session.execute(
                    text("select status, result from sales_cases where id = :i"), {"i": case2}
                )
            ).first()
        if row is not None:
            check("案例最终是已发布", row.status, "published")
            check_true("正文没有被那笔旧编辑覆盖", row.result is None, str(row.result))

    finally:
        async with SessionLocal() as session:
            await session.execute(
                text("delete from sales_cases where title like :p"), {"p": f"{MARKER}%"}
            )
            await session.commit()
        print()
        print(f"（已清理夹具：标题以 {MARKER} 开头的案例）")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("案例分享脱敏与并发编辑回归 全部通过")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
