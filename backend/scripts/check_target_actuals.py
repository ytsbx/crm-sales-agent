"""实绩快照：结账后历史数字不再随订单状态变（第三批 §4.1.5 后半，2026-10-06）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 这条修的是什么

报表原来是**每次打开现算**的。客户今年退掉去年的一张单，去年那一期的数字
就跟着变小——年底发奖金、做总结、给领导查数，拿的都是"当时那份报表"，
过几个月再看却变了，对账永远对不上。

`analytics_basis_snapshots` 当初只冻住了"客户集合与首次成交日"（老客池），
金额这一半没冻。这张 `analytics_actual_snapshots` 补齐另一半。

## 验证的是机制本身

不去造一堆订单再取消（那样测的是业务、不是快照），而是**直接改存档里的数**：
如果报表读的真是存档，改存档它就该跟着变；如果它偷偷重新算了，就不会变。
再走一次重算，应该回到实时算出来的值。

第 7 节是 2026-10-06 第二轮返工的回归（返工单第 2 / 3 / 4 条）：

- **#2** 确认回款改按**财务确认时间**归月（原来用到账日）；
  已确认却没记确认时间的**一笔不算**，只在报表上点名要求补录；
- **#2 连带**：跨月回款的那个月（只有回款、没有签单）必须也出现在报表上——
  老实现只按签单的键生成"补零行"，那笔回款会在报表上凭空消失；
- **#3** 下钻明细的归属改成**签单归属**，与汇总同源（原来明细按当前负责人，
  交接过的单子点开永远对不上）；
- **#4** 结账时**明细条目**跟着一起冻（新表 `analytics_actual_snapshot_items`），
  重算要清掉"上一版有、这一版没有"的陈旧汇总键。

跑法（需要后端在跑，因为要验 HTTP 的结账/重算接口）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \
      PYTHONPATH=. .venv/bin/python scripts/check_target_actuals.py
"""

import asyncio
import os
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import quote, urlparse

from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.analytics import targets as targets_svc
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKACT{int(time.time())}"
#: 用**上一个自然年**的 5 月：结账只允许针对已经过完的期间
PAST_YEAR = datetime.now(UTC).year - 1
PERIOD = f"{PAST_YEAR}-05"
#: 第二轮返工用：回款在 5 月到账、**6 月才被财务确认**，所以按确认时间该归 6 月。
#: 6 月没有任何签单 —— 正好验"某月只有回款没有签单，报表上也要有这一行"。
PERIOD2 = f"{PAST_YEAR}-06"
FAILURES: list[str] = []


def check(label: str, condition: object, expected: object = True) -> None:
    ok = condition == expected
    print(f'  {"OK  " if ok else "FAIL"} {label}：{condition!r}' + ("" if ok else f"（应为 {expected!r}）"))
    if not ok:
        FAILURES.append(label)


async def main():
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db
    assert settings.wecom_push_off and settings.dingtalk_push_off and not settings.scheduler_enabled, (
        "这条回归只能在推送全关的隔离库跑"
    )

    admin_token = login("admin", "admin123")

    async with SessionLocal() as s:
        order2_id = None
        closer_id = None
        boss = User(
            username=f"{MARKER}admin", name=f"{MARKER}负责人",
            password_hash="x", status="active",
        )
        s.add(boss)
        await s.flush()
        owner_id = boss.id
        customer = Customer(
            name=f"{MARKER}客户", owner_id=owner_id, status="active", pool_status="private",
        )
        s.add(customer)
        await s.flush()
        # 一张落在过去那一期的订单：签单额 1234.56
        order = SalesOrder(
            order_no=f"{MARKER}O1", customer_id=customer.id, owner_id=owner_id,
            sales_owner_id=owner_id, total_amount=Decimal("1234.56"), status="pending",
            created_at=datetime(PAST_YEAR, 5, 15, tzinfo=UTC),
        )
        s.add(order)
        await s.flush()
        customer_id, order_id = customer.id, order.id

        # ---- 第二轮返工（#2 / #3 / #4）的夹具 ----
        # 签单人（业绩归属人）与当前负责人**刻意分开**：只有这样，
        # "汇总按签单归属、明细按当前负责人"的错位才会露出来（返工单第 3 条）。
        closer = User(
            username=f"{MARKER}closer", name=f"{MARKER}签单人",
            password_hash="x", status="active",
        )
        s.add(closer)
        await s.flush()
        closer_id = closer.id
        order2 = SalesOrder(
            order_no=f"{MARKER}O2", customer_id=customer.id, owner_id=owner_id,
            sales_owner_id=closer_id, total_amount=Decimal("2000.00"), status="pending",
            created_at=datetime(PAST_YEAR, 5, 12, tzinfo=UTC),
        )
        s.add(order2)
        await s.flush()
        order2_id = order2.id
        # 回款 1：**5 月到账、6 月才确认** —— 按财务确认时间该归 6 月（#2 的核心）
        pay1 = PaymentRecord(
            order_id=order2_id, received_date=date(PAST_YEAR, 5, 20),
            received_amount=Decimal("500.00"), status="confirmed",
            confirmed_at=datetime(PAST_YEAR, 6, 5, 2, 0, tzinfo=UTC),
            confirmed_by=owner_id, created_at=datetime(PAST_YEAR, 5, 20, tzinfo=UTC),
        )
        # 回款 2：**状态已确认、却没有确认时间** —— 一笔都不能算，只提示补录
        pay2 = PaymentRecord(
            order_id=order2_id, received_date=date(PAST_YEAR, 5, 25),
            received_amount=Decimal("300.00"), status="confirmed",
            confirmed_at=None, created_by=owner_id,
            created_at=datetime(PAST_YEAR, 5, 25, tzinfo=UTC),
        )
        s.add_all([pay1, pay2])
        await s.flush()
        pay1_id, pay2_id = pay1.id, pay2.id
        await s.commit()

        user = CurrentUser(boss, permissions=set(), roles=[], data_scope="all")

        async def read_row():
            """读报表里这一期的行。

            **每次开全新会话**：真实请求就是这样。在同一个会话里读会被 ORM 的
            identity map 挡住（本项目 `expire_on_commit=False`，commit 不清缓存），
            原生 SQL 改完存档再读，拿到的还是旧对象——第一版就栽在这，
            "改了存档报表没反应"看着像功能没生效，其实是被缓存骗了。
            """
            async with SessionLocal() as fresh:
                result = await targets_svc.targets_with_actuals(fresh, user, PAST_YEAR)
                return next((r for r in result["rows"] if r["period"] == PERIOD), None)

        def report_row(period: str, user_id: int | None):
            """从报表里挑出某个 (期间, 人) 的行——**走真实接口**，不直接查库。"""
            status, body = call("GET", f"/sales-targets?year={PAST_YEAR}", token=admin_token)
            assert status == 200, body
            return next(
                (
                    r
                    for r in (body.get("data") or {}).get("rows", [])
                    if r["period"] == period and r.get("user_id") == user_id
                ),
                None,
            )

        def drilldown(metric: str, period: str, user_id: int | None):
            """调可追溯明细接口，返回它的 data。"""
            path = f"/sales-targets/drilldown?period={period}&metric={metric}"
            if user_id is not None:
                path += f"&user_id={user_id}"
            status, body = call("GET", path, token=admin_token)
            assert status == 200, body
            return body.get("data") or {}

        try:
            print("=== 1. 结账前：这一期是实时算的 ===")
            await s.execute(
                text("delete from analytics_actual_snapshots where period = :p"),
                {"p": PERIOD},
            )
            await s.commit()

            live = await read_row()
            check("这一期出现在报表上（有实绩就会补零行）", live is not None)
            check("结账前标的是「实时算」", live is not None and live["actual_frozen"], False)
            live_sales = live["sales_actual"] if live else None
            check("实时算出来的签单额 = 1234.56", live_sales, 1234.56)

            print("=== 2. 结账（写入存档）===")
            status, res = call("POST", f"/sales-targets/actuals/freeze?period={PERIOD}",
                               token=admin_token)
            check("结账接口成功", status, 200)
            # 别断言"总行数=4"：测试库里同一期还有别的夹具作用域，存的是 4 的整数倍。
            # 要验的是**本测试这个作用域**的四项指标都写进去了。
            async with SessionLocal() as fresh:
                mine = (
                    await fresh.execute(
                        text(
                            "select metric from analytics_actual_snapshots "
                            "where period = :p and scope_key = :k order by metric"
                        ),
                        {"p": PERIOD, "k": f"user:{owner_id}"},
                    )
                ).scalars().all()
            check("这个作用域写齐了全部指标（签单/回款/发货/新客/复购）",
                  sorted(mine),
                  ["new_customer", "received", "repeat_net", "sales", "shipped"])
            # 指标清单是 target_actuals.ACTUAL_METRICS，别硬编码数字：
            # 加了复购之后 4 变 5，写死 4 会在这里莫名其妙地红
            from app.modules.analytics.target_actuals import ACTUAL_METRICS

            check("写入行数是作用域数的整数倍（每个作用域写满全部指标）",
                  (res.get("data") or {}).get("rows", 0) % len(ACTUAL_METRICS), 0)

            frozen = await read_row()
            check("结账后报表标的是「存档值」", frozen is not None and frozen["actual_frozen"])
            check("数字与结账前一致", frozen["sales_actual"] if frozen else None, 1234.56)

            print("=== 3. 铁证：改存档，报表就该跟着变 ===")
            # 这一条是整套测试的关键 —— 如果报表偷偷重算了，改存档它不会有反应。
            # 同时又验证了「没设目标的人」也走存档：这一期的张三**没有目标行**，
            # 报表上是"有实绩没目标"的补零行，而补零行当初绕过了快照那条路
            # （第一版实现就漏了这里：设了目标的人数字冻住、没设的人照样漂移）。
            await s.execute(
                text(
                    "update analytics_actual_snapshots set actual_value = actual_value + 1000 "
                    "where period = :p and metric = 'sales'"
                ),
                {"p": PERIOD},
            )
            await s.commit()
            bumped = await read_row()
            check("改了存档 → 报表跟着变（证明读的确实是存档，没有偷偷重算）",
                  bumped["sales_actual"] if bumped else None, 2234.56)
            check("没设目标的行也算存档值（补零行没有绕过快照）",
                  bumped["target_id"] if bumped else "missing", None)

            print("=== 4. 重算：回到实时值，且必须填原因 ===")
            status, _ = call("POST", f"/sales-targets/actuals/refreeze?period={PERIOD}",
                             token=admin_token)
            check("重算不填原因被拒", status in (400, 422))

            # 原因里有中文，必须 quote：不编码的话 urllib 会在发包前
            # 拿 ascii 编 URL，直接抛 UnicodeEncodeError（不是被拒，是脚本崩了）
            status, res = call(
                "POST",
                f"/sales-targets/actuals/refreeze?period={PERIOD}"
                f"&reason={quote(MARKER + ' 口径修正')}",
                token=admin_token,
            )
            check("填了原因能重算", status, 200)
            recalc = await read_row()
            check("重算后回到实时算出来的 1234.56（不是改过的存档 2234.56）",
                  recalc["sales_actual"] if recalc else None, 1234.56)

            print("=== 5. 不该结的情形 ===")
            status, _ = call("POST", f"/sales-targets/actuals/freeze?period={PERIOD}",
                             token=admin_token)
            check("重复结账被拒（要改历史得走重算）", status, 422)

            this_month = datetime.now(UTC).strftime("%Y-%m")
            status, res = call("POST", f"/sales-targets/actuals/freeze?period={this_month}",
                               token=admin_token)
            check("当月不许结账（数据还在产生，冻了就是冻在半路上）", status, 422)

            status, _ = call("POST", "/sales-targets/actuals/freeze?period=2026-13",
                             token=admin_token)
            check("期间格式不对被拒", status in (400, 422))

            status, _ = call("POST", f"/sales-targets/actuals/freeze?period={PERIOD}",
                             token=login("zhangsan", "123456"))
            check("没有 settings:manage 的人不能结账", status, 403)

            print("=== 6. 重算留痕：审计里要看得出改了多少 ===")
            audit_row = (
                await s.execute(
                    text(
                        "select after_data from audit_logs "
                        "where action = 'refreeze_actuals' and after_data::text like :m "
                        "order by id desc limit 1"
                    ),
                    {"m": f"%{MARKER}%"},
                )
            ).first()
            check("重算动作写进了审计", audit_row is not None)
            check("审计里记了原因",
                  audit_row is not None and MARKER in str(audit_row[0]),
                  True)

            print("=== 7. 第二轮返工：#2 归月依据 / #3 归属同源 / #4 存档明细 ===")

            # --- #2：确认回款按**财务确认时间**归月，不是客户打款那天 ---
            jun = drilldown("received", PERIOD2, closer_id)
            may = drilldown("received", PERIOD, closer_id)
            check("5 月到账、6 月才确认的回款归到 6 月", jun.get("total"), 500.0)
            check("同一笔不再计进到账月（5 月）", may.get("total"), 0.0)

            report = (
                call("GET", f"/sales-targets?year={PAST_YEAR}", token=admin_token)[1].get("data")
                or {}
            )
            check("「已确认却没记确认时间」的回款被点名",
                  (report.get("missing_confirmed_at_count") or 0) >= 1)
            check("并且给了补录提示文案", bool(report.get("missing_confirmed_at_note")))

            # --- 跨月回款：那个月没有签单，报表上也必须有这一行 ---
            # 老实现只按签单的键生成补零行，于是 6 月这一行根本不存在，
            # 那笔回款明明计进了合计，却在报表上找不到它属于谁。
            row_jun = report_row(PERIOD2, closer_id)
            check("只有回款、没有签单的月份也出现在报表上", row_jun is not None)
            check("那一行的确认回款 = 500",
                  row_jun.get("received_actual") if row_jun else None, 500.0)
            check("那一行的签单额 = 0（这个月确实没有签单）",
                  row_jun.get("sales_actual") if row_jun else None, 0.0)

            # --- #3：汇总与明细必须是**同一个归属**（都是签单归属）---
            signed_may_closer = drilldown("signed", PERIOD, closer_id)
            signed_may_owner = drilldown("signed", PERIOD, owner_id)
            check("签单明细归到**签单人**",
                  any(item["id"] == order2_id for item in signed_may_closer.get("items", [])))
            check("当前负责人的签单明细里**没有**这张单（明细不再按现负责人算）",
                  any(item["id"] == order2_id for item in signed_may_owner.get("items", [])),
                  False)
            row_may_closer = report_row(PERIOD, closer_id)
            check("汇总行也归到签单人：签单额 = 2000",
                  row_may_closer.get("sales_actual") if row_may_closer else None, 2000.0)
            check("汇总 = 明细合计（同源，点开加得起来）",
                  row_jun.get("received_actual") if row_jun else None,
                  jun.get("total"))

            # --- #4：结账时明细跟着一起冻 ---
            # note 里带上 MARKER：这条审计才会被 finally 的清理语句扫到
            status, res = call(
                "POST",
                f"/sales-targets/actuals/freeze?period={PERIOD2}"
                f"&note={quote(MARKER + ' 结账')}",
                token=admin_token,
            )
            check("结账成功（这一期只有回款实绩，也要能结）", status, 200)
            check("结账时把明细条目一起存了",
                  ((res.get("data") or {}).get("items") or 0) >= 1)

            stored = drilldown("received", PERIOD2, closer_id)
            check("结账后下钻读的是存档", stored.get("source"), "snapshot")
            check("存档明细的归属人 = 签单人",
                  [item["owner_id"] for item in stored.get("items", [])], [closer_id])

            # 铁证：改存档里的明细金额，下钻就该跟着变 ——
            # 若它偷偷实时算，改存档不会有任何反应。
            await s.execute(
                text(
                    "update analytics_actual_snapshot_items set amount = amount + 100 "
                    "where period = :p and metric = 'received'"
                ),
                {"p": PERIOD2},
            )
            await s.commit()
            check("改了存档明细 → 下钻跟着变（证明读的确实是存档）",
                  drilldown("received", PERIOD2, closer_id).get("total"), 600.0)

            # --- #4：重算要清掉「上一版有、这一版没有」的陈旧汇总 ---
            await s.execute(
                text(
                    "insert into analytics_actual_snapshots "
                    "(period, scope_key, metric, actual_value, metric_basis_version, note) "
                    "values (:p, 'user:999999', 'sales', 777, 'stale', '陈旧残留')"
                ),
                {"p": PERIOD2},
            )
            await s.commit()
            status, res = call(
                "POST",
                f"/sales-targets/actuals/refreeze?period={PERIOD2}"
                f"&reason={quote(MARKER + ' 重算清旧键')}",
                token=admin_token,
            )
            check("重算成功", status, 200)
            check("重算报告里写了清掉几条陈旧汇总",
                  ((res.get("data") or {}).get("removed") or 0) >= 1)
            leftover = (
                await s.execute(
                    text(
                        "select count(*) from analytics_actual_snapshots "
                        "where period = :p and scope_key = 'user:999999'"
                    ),
                    {"p": PERIOD2},
                )
            ).scalar()
            check("陈旧汇总真的被删掉（不会永远停在上一版）", int(leftover or 0), 0)

            print("=== 8. 结账之后**源数据消失**，报表那一行也必须还在 ===")
            # 这一条是浏览器实测抓出来的：第一版把"补零行"的键全建在**实时**聚合上，
            # 于是结账之后客户退货 / 回款被驳回，实时值一没，**那一行整行不再生成**，
            # 后面"用存档覆盖"自然也无从发生 —— 报表上那一期的数字凭空消失。
            # 冻结的意义就是"结账之后这张报表不再变"，行没了比数字算错更糟。
            # （既有断言只测了"改存档看报表跟不跟着变"，没测"实时值消失"这条路径。）
            await s.execute(
                text("update payment_records set status = 'rejected' where id = :i"),
                {"i": pay1_id},
            )
            await s.commit()

            live_gone = report_row(PERIOD2, closer_id)
            check("源回款被驳回后，那一行**仍然在**报表上（不依赖实时值）",
                  live_gone is not None)
            check("而且显示的是存档值 500，不是实时值 0",
                  live_gone.get("received_actual") if live_gone else None, 500.0)

            stored_after = drilldown("received", PERIOD2, closer_id)
            check("明细也还在，并且仍读存档", stored_after.get("source"), "snapshot")
            check("存档明细合计仍是 500", stored_after.get("total"), 500.0)

        finally:
            # 清理顺序**自底向上**：快照明细 → 快照 → 回款 → 订单 → 客户 → 用户。
            # 快照明细表没有外键，但漏了它会在库里留一堆没有归属的孤行。
            for table in ("analytics_actual_snapshot_items", "analytics_actual_snapshots"):
                await s.execute(
                    text(f"delete from {table} where period in (:p, :p2)"),
                    {"p": PERIOD, "p2": PERIOD2},
                )
            if order2_id is not None:
                await s.execute(
                    text("delete from payment_records where order_id = :i"), {"i": order2_id}
                )
                await s.execute(
                    text("delete from sales_orders where id = :i"), {"i": order2_id}
                )
            await s.execute(text("delete from sales_orders where id = :i"), {"i": order_id})
            await s.execute(text("delete from customers where id = :i"), {"i": customer_id})
            await s.execute(
                text("delete from audit_logs where after_data::text like :m"),
                {"m": f"%{MARKER}%"},
            )
            for uid in (owner_id, closer_id):
                if uid is not None:
                    await s.execute(text("delete from users where id = :i"), {"i": uid})
            await s.commit()

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        raise SystemExit(1)
    print("OK 实绩快照：结账后金额冻结、重算须填原因并留痕、当月不可结")


if __name__ == "__main__":
    asyncio.run(main())
