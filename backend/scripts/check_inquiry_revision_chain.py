"""定制询价修订链：删除一版之后仍能以更早的版本为基础出新版（第十二批 12.6）。

跑法：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 PYTHONPATH=. .venv/bin/python scripts/check_inquiry_revision_chain.py

## 覆盖（主人 2026-10-08 拍板的口径）

内容来源与版本编号**分开处理**：

- **能不能修订**：只能对"当前有效版"（还活着的版本里编号最大的那个）再修订。
  删掉 V2 之后，"活着的"里最大是 V1 —— 于是 **V1 重新成为当前版**，
  允许以它为基础出新版。
- **新版本号**：取**整条链历史最大编号 + 1**，**把已删除的版本也算进来**。
  删掉 V2 后，2 号在页面上看不见了但在库里还占着（唯一索引不看删除标记），
  所以下一版是 **V3**，不是又一个 V2。
- V2 保持已删除：历史里看得到、标着"已删除"，但不恢复、不覆盖、不复用编号。
- 修订说明要注明"以 V1 为基础生成 V3"，操作日志里记 `from_version`。
- 继续用**链级行锁 + 唯一约束**防并发出两个 V3。

## 这一套件盯的正是"旧写法绿着漏掉"的那些组合

- 旧写法：`max(version)` 只算活着的行 → 删 V2 后算出最大是 1 → 新版本编成
  **2** → 撞唯一索引 **500**（而且等于复用了已删版本的号）。
- 旧写法：历史链只列活着的版本 → `V1 → V3` 中间跳号，看着像系统吃了东西。

夹具前缀 `CHK12R`，开头先清残留、结尾再清一次，可反复执行。
"""

import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request

from _test_support import require_api_base, require_isolated_db

require_isolated_db()

BASE = require_api_base()
URL = None
FAILURES = []
MARKER = "CHK12R"


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method, path, token=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return -1, {"error": str(e)}


def login(username, password):
    st, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if st != 200:
        print("登录失败", st, res)
        sys.exit(1)
    return res["data"]["access_token"]


# ---------------------------------------------------------------- 直连库（自建引擎）
async def _db(sql, params=None):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(URL, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            rows = result.all() if result.returns_rows else []
            await conn.commit()  # delete 也要真落地，不能靠连接关闭回滚
            return rows
    finally:
        await eng.dispose()


def db(sql, params=None):
    return asyncio.run(_db(sql, params))


def residue_roots():
    return [
        r[0]
        for r in db(
            "select distinct coalesce(root_id, id) from custom_inquiries where title like :m",
            {"m": f"{MARKER}%"},
        )
    ]


def purge():
    roots = residue_roots()
    if not roots:
        return
    ids = ",".join(str(int(x)) for x in roots)
    version_ids = [
        r[0]
        for r in db(f"select id from custom_inquiries where coalesce(root_id, id) in ({ids})")
    ]
    if version_ids:
        vids = ",".join(str(int(x)) for x in version_ids)
        db(f"delete from audit_logs where business_type='custom_inquiry' and business_id in ({vids})")
    db(f"delete from custom_inquiries where coalesce(root_id, id) in ({ids})")


# ---------------------------------------------------------------- 并发装置：外部持锁
def _hold_root_lock(root_id, ready, release):
    """独立连接锁住链条首版行、不提交；主线程放行后才提交。"""
    async def run():
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        eng = create_async_engine(URL, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                await conn.execute(
                    text("select id from custom_inquiries where id = :i for update"),
                    {"i": root_id},
                )
                ready.set()
                await asyncio.get_running_loop().run_in_executor(None, release.wait)
                await conn.commit()
        finally:
            await eng.dispose()

    asyncio.run(run())


def lock_waiter_count():
    rows = db(
        "select count(*) from pg_stat_activity "
        "where wait_event_type = 'Lock' and datname = current_database()"
    )
    return int(rows[0][0])


def main():
    global URL
    import os

    URL = os.environ["DATABASE_URL"]
    admin = login("admin", "admin123")
    cust = call("GET", "/customers?page=1&page_size=1", admin)[1]["data"]["items"][0]
    print(f"夹具客户：#{cust['id']} {cust['name']}")

    purge()
    try:
        # ============================================== 1. 建 V1 → 修订 V2 → 删 V2
        print("\n=== 1. 建 V1 → 修订 V2 → 删除 V2 ===")
        st, res = call("POST", "/custom-inquiries", admin,
                       {"title": f"{MARKER}-需求原始", "customer_id": cust["id"], "quantity": 100})
        check("建 V1", st, 200)
        v1 = res["data"]
        check("V1 版本号 = 1", v1["version"], 1)

        st, res = call("POST", f"/custom-inquiries/{v1['id']}/revise", admin,
                       {"revision_note": "第一次修订", "title": f"{MARKER}-改二"})
        check("修订出 V2", st, 200)
        v2 = res["data"]
        check("V2 版本号 = 2", v2["version"], 2)

        st, res = call("DELETE", f"/custom-inquiries/{v2['id']}", admin)
        check("删除 V2", st, 200)

        # ============================================== 2. 删后：V1 回到当前版、V2 仍在历史
        print("\n=== 2. 删掉 V2 之后的状态 ===")
        st, res = call("GET", f"/custom-inquiries/{v1['id']}/history", admin)
        check("历史链可读", st, 200)
        hist = {r["id"]: r for r in res["data"]}
        check_true("历史链里仍能看到已删除的 V2（不是凭空跳号）",
                   v2["id"] in hist, sorted(hist))
        if v2["id"] in hist:
            check("V2 标为「已删除」", hist[v2["id"]]["version_state_label"], "已删除")
            check("V2 的 is_deleted = True", hist[v2["id"]]["is_deleted"], True)
        check("V1 拿掉「已被新版取代」、回到当前版",
              hist[v1["id"]]["version_state_label"], "当前版")
        check("V1 的 is_superseded = False", hist[v1["id"]]["is_superseded"], False)

        # ============================================== 3. 以 V1 为基础生成 V3
        print("\n=== 3. 以 V1 为基础生成 V3（关键：编号要跳过已删除的 2 号）===")
        st, res = call("POST", f"/custom-inquiries/{v1['id']}/revise", admin,
                       {"revision_note": None})
        check("删 V2 后以 V1 修订 → 成功（旧写法这里撞唯一索引报 500）", st, 200)
        v3 = res["data"]
        check("新版本号 = 3（链内历史最大 + 1，含已删除的 V2）", v3["version"], 3)
        check("内容取自 V1（没误拿 V2 的内容）", v3["title"], f"{MARKER}-需求原始")
        check_true("修订说明注明「以 V1 为基础生成 V3」",
                   "以 V1 为基础生成 V3" in (v3["revision_note"] or ""),
                   v3["revision_note"])

        st, res = call("GET", f"/custom-inquiries/{v3['id']}/history", admin)
        hist = {r["id"]: r for r in res["data"]}
        check("V3 成为「当前版」", hist[v3["id"]]["version_state_label"], "当前版")
        check("V1 回到「已被新版取代」（历史版）",
              hist[v1["id"]]["version_state_label"], "已被新版取代")
        check("V2 仍是「已删除」（不被 V3 影响）",
              hist[v2["id"]]["version_state_label"], "已删除")
        check("链上版本号：1、2、3 各一条",
              sorted(r["version"] for r in hist.values()), [1, 2, 3])

        # 操作日志里记了"从第几版来"
        rows = db(
            "select after_data from audit_logs where business_type='custom_inquiry' "
            "and business_id = :i and action='revise'",
            {"i": v3["id"]},
        )
        after = rows[0][0] if rows else {}
        if isinstance(after, str):
            after = json.loads(after)
        check("操作日志记下基础版本 from_version = 1", after.get("from_version"), 1)

        # ============================================== 4. 再修订 → V4
        print("\n=== 4. 再修订 V3 → V4 ===")
        st, res = call("POST", f"/custom-inquiries/{v3['id']}/revise", admin,
                       {"revision_note": "第三次修订"})
        check("再修订成功", st, 200)
        check("V4 版本号 = 4", res["data"]["version"], 4)

        # ============================================== 5. 对照：历史版不许再改
        print("\n=== 5. 对照：对历史版（V1）发起修订 → 拒绝 ===")
        st, res = call("POST", f"/custom-inquiries/{v1['id']}/revise", admin,
                       {"revision_note": "偷偷改历史"})
        check("对已被取代的历史版修订 → 409", st, 409)

        # ============================================== 6. 并发 / 重复提交
        print("\n=== 6. 并发：同一条链同时两个修订请求（外部先锁住链条行）===")
        st, res = call("POST", "/custom-inquiries", admin,
                       {"title": f"{MARKER}-并发原始", "customer_id": cust["id"]})
        b1 = res["data"]
        st, res = call("POST", f"/custom-inquiries/{b1['id']}/revise", admin,
                       {"revision_note": "并发链 V2"})
        b2 = res["data"]
        call("DELETE", f"/custom-inquiries/{b2['id']}", admin)

        ready, release = threading.Event(), threading.Event()
        holder = threading.Thread(target=_hold_root_lock, args=(b1["id"], ready, release),
                                  daemon=True)
        holder.start()
        ready.wait(timeout=10)

        slots = [None, None]

        def worker(idx):
            slots[idx] = call("POST", f"/custom-inquiries/{b1['id']}/revise", admin,
                              {"revision_note": f"并发 {idx + 1}"})

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
        for t in threads:
            t.start()
        # 确认请求**真的**卡在锁上（不是串行跑过去的）
        waited = False
        for _ in range(60):
            if lock_waiter_count() > 0:
                waited = True
                break
            time.sleep(0.1)
        check_true("并发请求确实在等这把链级锁", waited, f"waiting={lock_waiter_count()}")
        release.set()
        for t in threads:
            t.join(timeout=30)
        holder.join(timeout=10)

        statuses = [s[0] for s in slots]
        print(f"   两个请求结果：{statuses}")
        check_true("并发结果里没有 5xx", all(s < 500 for s in statuses), statuses)
        check_true("至少一个成功", any(s == 200 for s in statuses), statuses)
        rows = db(
            "select version, deleted_at is not null from custom_inquiries "
            "where coalesce(root_id, id) = :r order by version",
            {"r": b1["id"]},
        )
        v3_count = sum(1 for v, _ in rows if v == 3)
        check("并发之后 v3 恰好一条（没并发出两个）", v3_count, 1)
        check("版本号仍不重复", len({v for v, _ in rows}), len(rows))

    finally:
        purge()
        print("\n夹具已清理")


if __name__ == "__main__":
    main()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("定制询价修订链：全部通过")
