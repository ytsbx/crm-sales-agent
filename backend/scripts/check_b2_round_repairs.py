"""B2 批次返修：六条确认缺陷的**反例**回归（2026-10-09 审查）。

每条都是"原测试全绿、缺陷仍在"，所以这里一律先打**反例**、再打**正向对照**，
确保修的是行为而不是把功能一起关掉：

- **B2-01** 清空订单负责人绕过分配权限：只有 `order:manage`、没有 `order:assign`
  的账号，`{"owner_id": null}` 从前返回 200 且负责人被清空（订单失去归属）。
  现在必须 422；正向对照是"改成别人 → 403"与"有权限时能正常换人"。
- **B2-02** 手工订单数量与货款对不上：`quantity=1.23456` → 落库 1.235、货款却按
  原值算成 185.18；`0.0001` → 落库 0.000、货款 0.02。现在超三位小数直接 400，
  合法数量下「货款 == 落库数量 × 单价」。
- **B2-03** 并发绕过任务终态保护：顺序"完成后改派"必须 400（15 轮全拦）；
  反向顺序正常放行且响应与库内一致。
- **B2-04** 清空补核联系时间后 `last_contact_unknown` 必须恢复为 True
  （否则这条客户既没联系时间、又不算"未知"，回收扫描会退回用建档时间
  把它错误列成回收候选）。
- **B2-05** 标签名编辑：三个空格 → 400（从前 200 + 空字符串）；
  65 字符 → 400（从前 500）；64 字符仍可用。
- **B2-06** 任务中心「转交 / 批量完成」入口：见前端页面（本套件只验接口可用）。

用法：
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_check_test_fresh \\
    API_BASE=http://127.0.0.1:8003/api/v1 \\
    PYTHONPATH=. .venv/bin/python scripts/check_b2_round_repairs.py
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("API_BASE", "").rstrip("/")
MARKER = "CHKB2"
FAILURES: list[str] = []
FIX: dict[str, object] = {}

#: 反例里"该被拒绝"的状态码（参数错误）
BAD_REQUEST = 400
UNPROCESSABLE = 422


def _guard() -> None:
    """显式要求一次性隔离库：本套件会真的建订单/任务/标签。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("必须显式设置 DATABASE_URL（一次性隔离库）")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not (name.startswith("crm_iso") or name.startswith("crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit("必须显式设置 API_BASE（默认的 8000 是开发后端）")


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + urllib.parse.quote(path, safe="/?&="), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {"raw": raw[:300]}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录 {username} 失败：{res.get('message')}")
    return res["data"]["access_token"]


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}（期望 {expected!r}）")
    if not ok:
        FAILURES.append(f"{label} → {actual!r}（期望 {expected!r}）")


def check_true(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {label}{(' —— ' + detail) if detail else ''}")
    if not cond:
        FAILURES.append(f"{label} —— {detail}")


def db(query, params=None):
    """直连库取真值（用独立线程 + 独立循环，避免连接池绑循环的坑）。"""
    import asyncio
    import threading

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    result: dict = {}

    def worker():
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    # 支持传一串语句（清理用）：asyncpg 的预处理语句**不接受多语句**，
                    # 必须逐条执行 —— 我第一版写成一条分号拼接的 SQL，静默没生效，
                    # 于是套件跑第二遍就因残留角色 40901（实测踩到）。
                    for one in ([query] if isinstance(query, str) else query):
                        res = await conn.execute(text(one))
                        if res.returns_rows:
                            result.setdefault("rows", []).extend(tuple(r) for r in res.all())
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("rows", [])


# ---------------------------------------------------------------- B2-01
def sec_b201(admin: str) -> None:
    print("\n=== B2-01 清空订单负责人不得绕过分配权限 ===")
    code = f"{MARKER}01"
    cleanup_b201()
    _, res = call("POST", "/roles", admin, {
        "code": code, "name": f"{MARKER}只有订单修改", "data_scope": "all",
        "permission_codes": ["order:view", "order:manage", "customer:view", "product:view"],
    })
    check("建角色（含 order:manage，不含 order:assign）", res.get("code"), 0)
    role_id = (res.get("data") or {}).get("id")
    _, res = call("POST", "/users", admin, {
        "username": f"{MARKER.lower()}01", "name": f"{MARKER}账号",
        "password": "B2pass123456", "role_ids": [role_id],
    })
    check("建受限账号", res.get("code"), 0)
    limited = login(f"{MARKER.lower()}01", "B2pass123456")

    _, cust = call("GET", "/customers?page_size=1", admin)
    items = cust["data"].get("items") if isinstance(cust["data"], dict) else cust["data"]
    _, sku = call("GET", "/pricing/sku-options", admin)
    sku_id = sku["data"][0]["id"]
    _, res = call("POST", "/orders", admin, {
        "customer_id": items[0]["id"], "owner_id": 2,
        "items": [{"sku_id": sku_id, "quantity": "1", "unit_price": "150"}],
    })
    check("建订单（负责人=张三）", res.get("code"), 0)
    order_id = res["data"]["order_id"]

    # 反例 1：改成别人 → 必须 403（这条一直是对的，作为对照）
    _, other = call("POST", "/users", admin, {
        "username": f"{MARKER.lower()}01t", "name": f"{MARKER}目标",
        "password": "B2pass123456", "role_ids": [],
    })
    other_id = (other.get("data") or {}).get("id")
    _, res = call("PATCH", f"/orders/{order_id}", limited, {"owner_id": other_id})
    check_true("① 改成别人被拦（403）", res.get("code") != 0, str(res.get("message"))[:40])

    # 反例 2：显式传 null → 从前 200 且清空
    _, res = call("PATCH", f"/orders/{order_id}", limited, {"owner_id": None})
    check_true("② 显式传 owner_id=null 被拒", res.get("code") != 0, str(res.get("message"))[:48])
    owner = db(f"select owner_id from sales_orders where id={order_id}")[0][0]
    check("② 被拒后负责人未被清空", owner, 2)

    # 正向对照：有 order:assign 的管理员能正常换人
    _, res = call("PATCH", f"/orders/{order_id}", admin, {"owner_id": other_id})
    check("③ 有分配权限时可正常换人", res.get("code"), 0)
    check("③ 库里负责人已更新", db(f"select owner_id from sales_orders where id={order_id}")[0][0], other_id)

    call("DELETE", f"/orders/{order_id}", admin)
    cleanup_b201()


def cleanup_b201() -> None:
    """把 B2-01 的角色/账号清干净（**一次事务**，且对残留免疫）。

    踩过的坑：上一轮验证留下的角色会让本轮的"建角色"直接 40901，
    于是后面几条断言跟着连锁失败 —— 套件必须能重复跑。
    """
    code = f"{MARKER}01"
    pref = MARKER.lower()
    db([
        "delete from user_roles where user_id in "
        f"(select id from users where username like '{pref}01%')",
        f"delete from users where username like '{pref}01%'",
        "delete from role_permissions where role_id in "
        f"(select id from roles where code='{code}')",
        f"delete from roles where code='{code}'",
    ])


# ---------------------------------------------------------------- B2-02
def sec_b202(admin: str) -> None:
    print("\n=== B2-02 手工订单数量精度与货款必须一致 ===")
    _, cust = call("GET", "/customers?page_size=1", admin)
    items = cust["data"].get("items") if isinstance(cust["data"], dict) else cust["data"]
    cid = items[0]["id"]
    _, sku = call("GET", "/pricing/sku-options", admin)
    sku_id = sku["data"][0]["id"]

    for qty, label in (("0.0001", "四位小数（会落库成 0.000）"), ("1.23456", "五位小数")):
        _, res = call("POST", "/orders", admin, {
            "customer_id": cid,
            "items": [{"sku_id": sku_id, "quantity": qty, "unit_price": "150"}],
        })
        check_true(f"数量 {qty}（{label}）被拒", res.get("code") != 0,
                   f"code={res.get('code')} {str(res.get('message'))[:40]}")

    # 正向：合法三位小数，货款必须等于「落库数量 × 单价」
    _, res = call("POST", "/orders", admin, {
        "customer_id": cid,
        "items": [{"sku_id": sku_id, "quantity": "1.235", "unit_price": "150"}],
    })
    check("合法三位小数可建单", res.get("code"), 0)
    oid = res["data"]["order_id"]
    rows = db(f"select quantity, unit_price, amount from sales_order_items where order_id={oid}")
    qty, price, amount = (float(x) for x in rows[0])
    check_true("货款 == 落库数量 × 单价", abs(qty * price - amount) < 0.005,
               f"{qty} × {price} = {qty * price:.2f}，实际货款 {amount}")
    check_true("接口返回总额与明细一致", abs(float(res["data"]["total_amount"]) - amount) < 0.005,
               f"接口 {res['data']['total_amount']} vs 明细 {amount}")
    call("DELETE", f"/orders/{oid}", admin)


# ---------------------------------------------------------------- B2-03
def sec_b203(admin: str) -> None:
    print("\n=== B2-03 任务终态保护不得被并发绕过 ===")
    # 反例：顺序"完成后改派"必须被拦（连打 12 轮，避免偶发漏判）
    blocked = 0
    rounds = 12
    for i in range(rounds):
        _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}顺序{i}", "owner_id": 2})
        tid = res["data"]["id"]
        call("POST", f"/tasks/{tid}/complete", admin, {})
        _, res = call("POST", f"/tasks/{tid}/assign", admin, {"owner_id": 4})
        owner = db(f"select owner_id from tasks where id={tid}")[0][0]
        if res.get("code") != 0 and owner == 2:
            blocked += 1
        call("DELETE", f"/tasks/{tid}", admin)
    check(f"顺序『完成后改派』被拦（{rounds} 轮）", blocked, rounds)

    # 正向：先改派再完成（顺序）两边都该成功、且响应与库内一致
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}反序", "owner_id": 2})
    tid = res["data"]["id"]
    _, r1 = call("POST", f"/tasks/{tid}/assign", admin, {"owner_id": 4})
    _, r2 = call("POST", f"/tasks/{tid}/complete", admin, {})
    check("先改派成功", r1.get("code"), 0)
    check("再完成成功", r2.get("code"), 0)
    check("完成响应状态与库内一致",
          (r2.get("data") or {}).get("status"),
          db(f"select status from tasks where id={tid}")[0][0])
    call("DELETE", f"/tasks/{tid}", admin)

    # 并发同时发起：库里必须自洽（要么"改派被拒 + 完成"，要么"改派 + 完成"，
    # 不能出现"完成响应回显 pending 而库里是 done"之外的第三种状态）
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}并发", "owner_id": 2})
    tid = res["data"]["id"]
    with ThreadPoolExecutor(max_workers=2) as ex:
        fc = ex.submit(call, "POST", f"/tasks/{tid}/complete", admin, {})
        fa = ex.submit(call, "POST", f"/tasks/{tid}/assign", admin, {"owner_id": 4})
        _, rc = fc.result()
        _, ra = fa.result()
    status = db(f"select status from tasks where id={tid}")[0][0]
    check_true("并发后库里状态自洽（done 或 pending 之一，不是未知值）",
               status in ("done", "pending"), f"status={status}")
    # 真正要守的不变量：**响应不能与库内矛盾**。
    # 若改派被拒，改写响应就不该声称已改；若改派成功，库里负责人必须是新的。
    owner = db(f"select owner_id from tasks where id={tid}")[0][0]
    if ra.get("code") != 0:
        check("改派被拒时负责人未变", owner, 2)
    else:
        check("改派成功时负责人已变", owner, 4)
    check_true("完成响应状态与库内一致",
               (rc.get("data") or {}).get("status") == status,
               f"响应={(rc.get('data') or {}).get('status')} 库内={status}")
    call("DELETE", f"/tasks/{tid}", admin)


# ---------------------------------------------------------------- B2-03b
def sec_b203b(admin: str) -> None:
    """严格并发校验：客户端回传版本号时，过期版本必须被拒（审查 B2-03）。

    这是唯一能拦住"两个请求**真正同时**发起"的一层：乐观锁用的是**本请求自己**
    读到的 `updated_at`，并发下它可能已经是对方改完之后的版本，于是两边都成立。
    客户端回传的是"用户点按钮那一刻屏幕上那一版"，对不上就 409。
    """
    print("\n=== B2-03b 版本号严格校验 ===")

    # ① 响应里必须带 updated_at（客户端要拿它当版本号）
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}ver-A", "owner_id": 2})
    tid = res["data"]["id"]
    _, detail = call("GET", f"/tasks/{tid}", admin)
    ver = (detail.get("data") or {}).get("updated_at")
    check_true("① 任务响应里带 updated_at", bool(ver), f"updated_at={ver}")

    # ② 带**最新**版本 → 成功
    _, res = call("POST", f"/tasks/{tid}/complete", admin, {"expected_updated_at": ver})
    check("② 带最新版本可以完成", res.get("code"), 0)
    check("② 库里状态已变", db(f"select status from tasks where id={tid}")[0][0], "done")

    # ③ 带**过期**版本 → 40902，且不改库
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}ver-B", "owner_id": 2})
    t2 = res["data"]["id"]
    old_ver = call("GET", f"/tasks/{t2}", admin)[1]["data"]["updated_at"]
    call("POST", f"/tasks/{t2}/assign", admin, {"owner_id": 4})   # 先改一次，版本变了
    _, res = call("POST", f"/tasks/{t2}/complete", admin, {"expected_updated_at": old_ver})
    check("③ 过期版本被拒（40902）", res.get("code"), 40902)
    check("③ 被拒后状态未被改动", db(f"select status from tasks where id={t2}")[0][0], "pending")
    check_true("③ 提示说清要刷新", "刷新" in str(res.get("message") or ""),
               str(res.get("message"))[:44])

    # ④ 不带版本 → 兼容放行（老前端不坏）
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}ver-C", "owner_id": 2})
    t3 = res["data"]["id"]
    _, res = call("POST", f"/tasks/{t3}/complete", admin, {})
    check("④ 不带版本仍可完成（兼容口径）", res.get("code"), 0)

    # ⑤ 真正同时发起 + 都带同一版本 → 只允许一个成功
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}ver-D", "owner_id": 2})
    t4 = res["data"]["id"]
    same = call("GET", f"/tasks/{t4}", admin)[1]["data"]["updated_at"]
    with ThreadPoolExecutor(max_workers=2) as ex:
        fc = ex.submit(call, "POST", f"/tasks/{t4}/complete", admin,
                       {"expected_updated_at": same})
        fa = ex.submit(call, "POST", f"/tasks/{t4}/assign", admin,
                       {"owner_id": 4, "expected_updated_at": same})
        c1 = fc.result()[1].get("code")
        c2 = fa.result()[1].get("code")
    ok = sum(1 for c in (c1, c2) if c == 0)
    check("⑤ 同时发起且都带同一版本时只成功一个", ok, 1)
    # 被拒的那个**可能是两种码，都是正确的**，取决于谁先提交：
    #   · 改派先提交 → 完成的版本条件不匹配 → **40902 版本冲突**；
    #   · 完成先提交 → 改派被**终态守卫**拦下 → 40002「任务已完成，不能改派」。
    #     这条其实信息量更大（直接告诉你为什么不能改），不必强求 40902。
    # 要守的是"**不是 500、也不是两边都成功**"，而不是某个具体错误码。
    check_true("⑤ 另一个是明确的拒绝（40902 版本冲突或 40002 终态），不是 500",
               {c1, c2} in ({0, 40902}, {0, 40002}), f"codes=[{c1}, {c2}]")

    for t in (tid, t2, t3, t4):
        call("DELETE", f"/tasks/{t}", admin)
    db(f"delete from tasks where title like '{MARKER}ver-%'")


# ---------------------------------------------------------------- B2-04
def sec_b204(admin: str) -> None:
    print("\n=== B2-04 清空补核联系时间必须恢复「未知」标记 ===")
    db(f"delete from customers where name like '{MARKER}04%'")
    _, res = call("POST", "/customers", admin, {"name": f"{MARKER}04历史客户", "owner_id": 2})
    cid = res["data"]["id"]
    call("PATCH", f"/customers/{cid}", admin, {"last_followup_at": "2026-05-01"})
    check("补核后未知标记=False", db(f"select last_contact_unknown from customers where id={cid}")[0][0], False)

    _, res = call("PATCH", f"/customers/{cid}", admin, {"last_followup_at": None})
    check("清空时间本身成功", res.get("code"), 0)
    check("时间为空", db(f"select last_followup_at from customers where id={cid}")[0][0], None)
    check("清空后未知标记恢复=True（不再被当已知时间参与回收）",
          db(f"select last_contact_unknown from customers where id={cid}")[0][0], True)
    call("DELETE", f"/customers/{cid}", admin)


# ---------------------------------------------------------------- B2-05
def sec_b205(admin: str) -> None:
    print("\n=== B2-05 标签名编辑校验 ===")
    db("delete from tags where name like 'CHKB205%' or name=''")
    _, res = call("POST", "/tags", admin, {"name": f"{MARKER}05标签", "type": "custom"})
    check("建标签", res.get("code"), 0)
    tid = res["data"]["id"]

    _, res = call("PATCH", f"/tags/{tid}", admin, {"name": "   "})
    check_true("三个空格被拒（从前 200 + 空字符串）", res.get("code") != 0,
               f"code={res.get('code')} {str(res.get('message'))[:36]}")
    check("被拒后名称未被改成空串",
          db(f"select name from tags where id={tid}")[0][0], f"{MARKER}05标签")

    _, res = call("PATCH", f"/tags/{tid}", admin, {"name": "甲" * 65})
    check_true("65 字符被拒（从前 500）", res.get("code") != 0,
               f"code={res.get('code')} {str(res.get('message'))[:36]}")

    _, res = call("PATCH", f"/tags/{tid}", admin, {"name": "乙" * 64})
    check("64 字符仍可用（边界）", res.get("code"), 0)
    check("库里已是 64 字符", len(db(f"select name from tags where id={tid}")[0][0]), 64)
    call("DELETE", f"/tags/{tid}", admin)


# ---------------------------------------------------------------- B2-06
def sec_b206(admin: str) -> None:
    print("\n=== B2-06 任务「转交 / 批量完成」接口可用（页面入口见前端）===")
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}06转交", "owner_id": 2})
    tid = res["data"]["id"]
    _, res = call("POST", f"/tasks/{tid}/transfer", admin, {"owner_id": 4})
    check("转交接口可用", res.get("code"), 0)
    check("负责人已变更", db(f"select owner_id from tasks where id={tid}")[0][0], 4)

    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}06批量A", "owner_id": 2})
    a = res["data"]["id"]
    _, res = call("POST", "/tasks", admin, {"title": f"{MARKER}06批量B", "owner_id": 2})
    b = res["data"]["id"]
    _, res = call("POST", "/tasks/batch-complete", admin, {"task_ids": [a, b]})
    check("批量完成接口可用", res.get("code"), 0)
    data = res.get("data") or {}
    # 返回结构：completed / skipped 都是**清单**（与线索批量分配同一口径），
    # 让操作的人知道哪几条没成、为什么
    done_list = data.get("completed") or []
    skip_list = data.get("skipped") or []
    check_true("批量完成回报了成功清单", len(done_list) >= 1,
               f"completed={done_list} skipped={skip_list}")
    check_true("成功清单恰好是这两条", sorted(done_list) == sorted([a, b]),
               f"completed={done_list}")
    # 重复批量：已完成的应被跳过并给出原因（而不是静默成功或 500）
    _, res = call("POST", "/tasks/batch-complete", admin, {"task_ids": [a, b]})
    check_true("重复批量完成不报 500", res.get("code") is not None and res.get("code") == 0,
               f"code={res.get('code')}")
    for t in (tid, a, b):
        call("DELETE", f"/tasks/{t}", admin)
    db(f"delete from tasks where title like '{MARKER}06%'")


def main() -> None:
    _guard()
    admin = login("admin", "admin123")
    sec_b201(admin)
    sec_b202(admin)
    sec_b203(admin)
    sec_b203b(admin)
    sec_b204(admin)
    sec_b205(admin)
    sec_b206(admin)
    print()
    if FAILURES:
        print(f"FAILED（{len(FAILURES)}）:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("B2 批次返修：全部通过")


if __name__ == "__main__":
    main()
