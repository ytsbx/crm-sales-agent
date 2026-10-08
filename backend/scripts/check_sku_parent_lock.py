#!/usr/bin/env python
"""SKU 创建入口与「产品删除」的并发互斥（回收站复审返修 RB03 的后半段）。

守的问题
--------
回收站那批（提交 73c648d）给「嵌套新增 SKU」加上了产品行锁，**另外两个入口没跟着改**：

- 扁平新增 `POST /skus`
- 批量导入 `POST /skus/import`

两者都是"先读一眼产品在不在 → 再把 SKU 插进去"，中间不持锁。而产品删除是
「软删产品 + 连带软删它名下 SKU」。两边同时进行时各看各的旧世界，收尾就留下
**挂在已删产品下的有效 SKU**：产品列表里看不见它，SKU 详情却还打得开。

同时守第二部分：批量导入涉及多个产品时，取锁顺序要统一（按产品 id 升序）。
不统一的话，两份"产品出现顺序相反"的文件同时导入会互相等（死锁）。

## 为什么必须真库真接口

这三件事只有真库能证明：

1. `SELECT ... FOR UPDATE` 的互斥 —— SQLite 直接忽略 FOR UPDATE；
2. 导入的取锁发生在**后端进程**里，离线测不到；
3. "产品被删"这件事必须由**另一个并发事务**制造出来，不是靠 mock。

## 跑法（必须显式给 API_BASE，且不能指向 8000 开发后端）

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
    DATABASE_URL=postgresql+asyncpg://crm:...@127.0.0.1:5432/crm_iso_test_x \\
      PYTHONPATH=. .venv/bin/python scripts/check_sku_parent_lock.py

「删除方」用一个**独立的、长期不提交的事务**扮演：先锁住产品行、把它标成已删除，
然后才发被测请求。这样才能确定性地复现"请求读到的是旧状态、真正插入时才撞上删除"，
而**不是靠 sleep 碰运气** —— 等待是靠轮询 `pg_stat_activity` 的
`wait_event_type='Lock'` 确认的。
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

BASE = os.environ.get("API_BASE", "")
if not BASE:
    raise SystemExit(
        "必须显式设置 API_BASE（本套件会写夹具并调接口，不能默认打到开发后端 8000）"
    )
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")

PREFIX = "CHKSKULK"
STAMP = str(int(time.time()))
FAILURES: list[str] = []

#: 扮演"另一个正在删产品的事务"用的连接。
#:
#: 用 NullPool 而不是默认池：这个连接要**长期占着一个未提交的事务**，
#: 万一被连接池借给别的查询，那些查询会读到未提交的中间状态 —— 排查起来
#: 会像"数据莫名其妙自己变了"。
engine = create_async_engine(settings.database_url, poolclass=NullPool)


# ------------------------------------------------------------------ 基础工具


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None, files=None):
    """发一个请求。`files` 非空时走 multipart（表单字段从 `body` 取）。"""
    data = None
    headers = {}
    if files is not None:
        boundary = "----CHKSKULK" + STAMP
        parts = []
        for name, (filename, content) in files.items():
            parts.append(f"--{boundary}\r\n")
            parts.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            )
            parts.append("Content-Type: text/csv\r\n\r\n")
            parts.append(content)
            parts.append("\r\n")
        for name, value in (body or {}).items():
            parts.append(f"--{boundary}\r\n")
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
            parts.append(str(value))
            parts.append("\r\n")
        parts.append(f"--{boundary}--\r\n")
        data = "".join(parts).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(BASE + path, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip().startswith("{") else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"code": None, "message": raw[:200]}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


def csv_upload(content: str, filename: str = "skus.csv"):
    return {"file": (filename, content)}


async def fire(**kwargs):
    """把阻塞的 HTTP 调用丢进线程，返回一个可 await 的 task（用于"并发"）。"""
    return asyncio.create_task(asyncio.to_thread(lambda: call(**kwargs)))


# ------------------------------------------------------------------ 并发观测


async def waiting_for_lock(timeout: float = 15.0) -> int:
    """轮询到"确实有会话在等锁"为止，返回等待中的会话数（超时返回 0）。

    **不用 sleep 猜**：只有真的观察到 `wait_event_type='Lock'` 才算数，
    否则这个套件就成了"睡够了就假定对方在等"的假绿。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "select count(*) from pg_stat_activity "
                        "where datname = current_database() "
                        "and wait_event_type = 'Lock' and state = 'active'"
                    )
                )
            ).scalar_one()
        if count:
            return int(count)
        await asyncio.sleep(0.15)
    return 0


async def can_lock_product_nowait(product_id: int) -> bool:
    """此刻能否**立刻**拿到这个产品的行锁（`FOR UPDATE NOWAIT`）。

    这是判断"别的会话有没有已经攥住这一行"的确定性办法。

    ⚠️ 不要用"数 `pg_locks` 里的 tuple 条目"来判断（实测踩过）：行锁在只有
    一个持有者时**根本不进锁表**（靠行头的 xmax 标记实现），而**等待者**反而会
    被记上一条 —— 数出来的数与"谁锁住了什么"并不对应，会得出相反的结论。
    `NOWAIT` 问的是数据库本身："这行现在锁得上吗"，答案唯一。
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(
                text("select id from products where id = :p for update nowait"),
                {"p": product_id},
            )
            return True
        except Exception:  # noqa: BLE001 —— 锁不上就是"被占着"，这正是要判的
            return False
        finally:
            if trans.is_active:
                await trans.rollback()


class PendingProductDelete:
    """扮演"另一个事务正在删这些产品，但还没提交"。

    只做两件事：拿住产品行锁、把 `deleted_at` 改掉 —— 目的就是让被测入口卡在
    `lock_product` 上。真实删除（`delete_product`）还会连带软删名下 SKU，
    那部分由 §5 用真接口单独验证，这里不重复。
    """

    def __init__(self) -> None:
        self._conn = None
        self._trans = None

    async def __aenter__(self) -> "PendingProductDelete":
        self._conn = await engine.connect()
        self._trans = await self._conn.begin()
        return self

    async def hold(self, product_id: int, *, mark_deleted: bool = True) -> None:
        """拿住产品行锁；`mark_deleted=True` 时顺带把它标成已删除。

        顺序用例（§4）传 `mark_deleted=False`：那条只想验证"导入先去要哪个产品"，
        产品本身要留着有效，否则导入就算拿到锁也会因为"产品已删"而跳过它，
        两件事混在一起就说不清了。
        """
        await self._conn.execute(
            text("select id from products where id = :p for update"), {"p": product_id}
        )
        if mark_deleted:
            await self._conn.execute(
                text("update products set deleted_at = now() where id = :p"),
                {"p": product_id},
            )

    async def commit(self) -> None:
        await self._trans.commit()

    async def __aexit__(self, *exc) -> None:
        if self._trans is not None and self._trans.is_active:
            await self._trans.rollback()
        if self._conn is not None:
            await self._conn.close()


# ------------------------------------------------------------------ 查库工具


async def create_product(token: str, name: str) -> int:
    status, res = call("POST", "/products", token=token, body={"name": name})
    if res.get("code") != 0:
        raise SystemExit(f"建产品失败（{status}）：{res.get('message')}")
    return res["data"]["id"]


async def sku_row(sku_code: str):
    """返回 `(id, deleted_at)`；没有这行时返回 None（与 deleted_at 为 NULL 区分开）。"""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("select id, deleted_at from skus where sku_code = :c"), {"c": sku_code}
            )
        ).first()
    return None if row is None else (int(row[0]), row[1])


async def live_sku_count(product_name_like: str) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "select count(*) from skus s join products p on p.id = s.product_id "
                        "where s.deleted_at is null and p.name like :p"
                    ),
                    {"p": f"{product_name_like}%"},
                )
            ).scalar_one()
        )


async def cleanup() -> None:
    """按前缀物理删掉本次造的 SKU 与产品（自底向上）。"""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "delete from skus where product_id in "
                "(select id from products where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await conn.execute(text("delete from products where name like :p"), {"p": f"{PREFIX}%"})
        await conn.execute(
            text(
                "delete from audit_logs where business_type in ('product', 'sku') "
                "and (coalesce(before_data::text, '') like :m "
                "or coalesce(after_data::text, '') like :m)"
            ),
            {"m": f"%{PREFIX}%"},
        )


# ------------------------------------------------------------------ 用例


async def concurrent_case(*, product_id: int, request: dict) -> tuple[int, dict, int]:
    """跑一遍「外部事务删产品（不提交）→ 发请求 → 确认卡在锁 → 提交 → 收响应」。

    返回 `(http 状态, 响应体, 等待中的会话数)`。
    """
    async with PendingProductDelete() as pending:
        await pending.hold(product_id)
        task = await fire(**request)
        waited = await waiting_for_lock()
        await pending.commit()
        status, body = await task
    return status, body, waited


async def main() -> int:
    admin = login("admin", "admin123")

    print("== 0. 夹具 ==")
    # 甲、乙专门给"取锁顺序"那条用：甲先建 → id 更小，"按 id 升序"就会先要甲
    name_a = f"{PREFIX}甲-{STAMP}"
    name_b = f"{PREFIX}乙-{STAMP}"
    pa = await create_product(admin, name_a)
    pb = await create_product(admin, name_b)
    check_true("产品甲的 id 小于乙（顺序用例的前提）", pa < pb, f"甲={pa} 乙={pb}")

    # ---------------- §1 嵌套新增 × 产品删除（对照：这条本来就加了锁） ----------------
    print()
    print("== 1. 嵌套新增 SKU × 产品删除并发 ==")
    p1 = await create_product(admin, f"{PREFIX}并发嵌套-{STAMP}")
    code1 = f"{PREFIX}N1-{STAMP}"
    status, _, waited = await concurrent_case(
        product_id=p1,
        request=dict(
            method="POST",
            path=f"/products/{p1}/skus",
            token=admin,
            body={"sku_code": code1, "name": "并发嵌套"},
        ),
    )
    check_true("① 观察到真实锁等待（不是靠延时）", waited >= 1, f"等待中的会话 {waited}")
    check("① 删除先提交 → 新增被拒", status, 404)
    check("① 库里没有这个 SKU", await sku_row(code1), None)

    # ---------------- §2 扁平新增 × 产品删除（本次修复的核心） ----------------
    print()
    print("== 2. 扁平新增 SKU × 产品删除并发（POST /skus）==")
    p2 = await create_product(admin, f"{PREFIX}并发扁平-{STAMP}")
    code2 = f"{PREFIX}F2-{STAMP}"
    status, _, waited = await concurrent_case(
        product_id=p2,
        request=dict(
            method="POST",
            path="/skus",
            token=admin,
            body={"product_id": p2, "sku_code": code2, "name": "并发扁平"},
        ),
    )
    check_true("② 观察到真实锁等待（不是靠延时）", waited >= 1, f"等待中的会话 {waited}")
    check("② 删除先提交 → 新增被拒", status, 404)
    check("② 库里没有这个 SKU（修复前这里会留下孤儿）", await sku_row(code2), None)

    # ---------------- §3 批量导入 × 产品删除 ----------------
    print()
    print("== 3. 批量导入 SKU × 产品删除并发（POST /skus/import）==")
    name_imp_gone = f"{PREFIX}并发导入被删-{STAMP}"
    name_imp_ok = f"{PREFIX}并发导入对照-{STAMP}"
    p3 = await create_product(admin, name_imp_gone)
    await create_product(admin, name_imp_ok)
    code3a = f"{PREFIX}I3A-{STAMP}"
    code3b = f"{PREFIX}I3B-{STAMP}"
    # 文件里两行：第一行的产品会被并发删掉，第二行的产品是好的
    csv3 = (
        "SKU编码,产品名称\n"
        f"{code3a},{name_imp_gone}\n"
        f"{code3b},{name_imp_ok}\n"
    )
    status, body, waited = await concurrent_case(
        product_id=p3,
        request=dict(
            method="POST", path="/skus/import", token=admin, files=csv_upload(csv3)
        ),
    )
    data = body.get("data") or {}
    check_true("③ 观察到真实锁等待（不是靠延时）", waited >= 1, f"等待中的会话 {waited}")
    check("③ 导入整体仍返回成功（逐行处理）", status, 200)
    check("③ 被删产品那行**没有**被报告成功", data.get("failed_count"), 1)
    check("③ 合法行照常新增", data.get("created_count"), 1)
    check(
        "③ 新增的正是合法那一行",
        [row.get("sku_code") for row in (data.get("created") or [])],
        [code3b],
    )
    check("③ 库里没有被删产品下的新 SKU", await sku_row(code3a), None)
    check_true("③ 合法行的 SKU 确实建了", (await sku_row(code3b)) is not None)

    # ---------------- §4 取锁顺序：按产品 id 升序，而非文件里的出现顺序 ----------------
    print()
    print("== 4. 导入取锁顺序（文件里乙在前、甲在后；外部只锁住甲）==")
    code4a = f"{PREFIX}O4A-{STAMP}"
    code4b = f"{PREFIX}O4B-{STAMP}"
    csv4 = (
        "SKU编码,产品名称\n"
        f"{code4a},{name_b}\n"
        f"{code4b},{name_a}\n"
    )
    async with PendingProductDelete() as pending:
        await pending.hold(pa, mark_deleted=False)  # 只锁住甲（id 更小），不标删、不提交
        task = await fire(
            method="POST", path="/skus/import", token=admin, files=csv_upload(csv4)
        )
        waited = await waiting_for_lock()
        # 卡住的这一刻，乙应当**还能被别人立刻锁住** —— 拿得到就说明导入压根还没碰它，
        # 也就是"它一上来要的是甲（id 最小的）"。反过来若按文件顺序先要乙，
        # 乙此刻已经握在导入手里，这里会拿不到。
        b_still_free = await can_lock_product_nowait(pb)
        await pending.commit()
        status, body = await task
    data4 = body.get("data") or {}
    check_true("④ 观察到真实锁等待（不是靠延时）", waited >= 1, f"等待中的会话 {waited}")
    check_true(
        "④ 卡住时乙仍未被导入占用 → 说明它一上来就要的是甲（id 最小的）",
        b_still_free,
    )
    check("④ 甲拿回锁后两行都成功", [data4.get("created_count"), data4.get("failed_count")], [2, 0])

    # ---------------- §5 新增先完成 → 随后删产品要连带删掉它 ----------------
    print()
    print("== 5. 新增先完成，随后删产品（连带软删）==")
    p5 = await create_product(admin, f"{PREFIX}先后-{STAMP}")
    code5 = f"{PREFIX}S5-{STAMP}"
    status, _ = call(
        "POST",
        "/skus",
        token=admin,
        body={"product_id": p5, "sku_code": code5, "name": "先后用例"},
    )
    check("⑤ 新增成功", status, 200)
    status, _ = call("DELETE", f"/products/{p5}", token=admin)
    check("⑤ 删产品成功", status, 200)
    row = await sku_row(code5)
    check_true(
        "⑤ 随后删产品把它名下的 SKU 一起软删了",
        row is not None and row[1] is not None,
        f"库里那行 = {row}",
    )

    # ---------------- §6 收尾一致性 ----------------
    print()
    print("== 6. 收尾：不该存在「产品已删、SKU 未删」 ==")
    async with engine.connect() as conn:
        orphan_local = int(
            (
                await conn.execute(
                    text(
                        "select count(*) from skus s join products p on p.id = s.product_id "
                        "where p.deleted_at is not null and s.deleted_at is null "
                        "and p.name like :p"
                    ),
                    {"p": f"{PREFIX}%"},
                )
            ).scalar_one()
        )
        orphan_all = int(
            (
                await conn.execute(
                    text(
                        "select count(*) from skus s join products p on p.id = s.product_id "
                        "where p.deleted_at is not null and s.deleted_at is null"
                    )
                )
            ).scalar_one()
        )
    check("⑥ 本次夹具范围内没有孤儿 SKU", orphan_local, 0)
    print(f"  （全库同类记录 {orphan_all} 条，仅供参考，不计入判定）")

    # ---------------- §7 清理 ----------------
    print()
    print("== 7. 清理 ==")
    await cleanup()
    check("⑦ 夹具已清干净", await live_sku_count(PREFIX), 0)

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
