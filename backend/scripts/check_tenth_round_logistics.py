#!/usr/bin/env python
"""物流试算「记一条」时，那条记录记在谁名下（第十批复审 10.5）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 守的是什么

`POST /logistics/calculate` 传 `save=true` 时会把这次试算落成一条记录
（`logistics_quotes`），记录上可以挂一个客户、也可以不挂（纯比价）。

复审发现：**落库这一步把传进来的 `customer_id` / `opportunity_id` 原样写进库，
一个字都不查**。同一个模块的列表（`list_quotes`）与详情（`get_quote_or_404`
外面还套一层客户可见性）早就判了范围 —— 只有落库这一处漏着，两个入口口径
本来就不该不一样。

后果每一条在下面都有对应断言。**这四条是实测出来的**（把校验拔掉跑一遍，
见文末"反向验证实测"），不是照直觉推的 —— 其中第 2 条尤其反直觉：

1. **能把自己的试算挂到同事的客户上**：接口 200、真落了库
   （`quote_count` 当场从 2 变 3）。那位同事按客户筛试算时（`list_quotes`
   正是按 `customer_id` 收范围）会看见一条**不是他算的**记录。
2. **能挂到一个根本不存在的客户编号上 → 接口 500。** 客户 id 上有外键
   （`logistics_quotes_customer_id_fkey`），数据库确实挡住了这一行，
   但用户看到的是"服务器内部错误"，而不是"这个客户不存在"。
   （最初以为会变成"谁都看不见的隐形垃圾"——**错了**：外键先一步拦住了，
   只是以 500 的形式。）
3. **商机与客户不对号时也能落下去**（200），留下一行自相矛盾的记录。
4. **已删客户 / 已删商机同样能挂上去**（200）—— 软删不触发外键。

## 为什么新建套件而不是塞进现成的

物流试算的 calculate/save 这条链在此之前**没有任何接口级套件覆盖**。
`tests/test_logistics_quote.py` 只是纯函数单测（测 `quote_rate` 的算术），
`grep -rn "logistics/calculate" scripts/*.py` 在新建本套件之前是**零命中**。

## 断言写法上的两个刻意选择

- **"被拒"不只看返回码，还数一遍库里的条数。** 拒绝也可能是"先写进去再抛错"，
  靠事务回滚兜住 —— 那种实现下用户以为没保存，其实留下过半截数据。
- **试算输入用 override 固定重量与体积**，不吃所选 SKU 上的字段，
  免得变成"数据依赖型脆弱用例"（别的套件改了那个 SKU 就跟着漂）。

## 反向验证实测（2026-10-08：把 `_assert_saved_source_visible` 的调用拔掉跑一遍）

12 项当场报红，而且**后果看得见**，不只是"断言红了"：

| 场景 | 拔掉校验后 | 该有的样子 |
|---|---|---|
| 挂同事的客户 | 200，真落库（2 → 3） | 403，不落库 |
| 挂不存在的客户 id | **500**（外键违反） | 404 |
| 挂已删客户 | 200，真落库（3 → 4） | 404 |
| 商机与客户不对号 | 200，真落库（5 → 6） | 422 |
| 商机不存在 | 200，真落库（6 → 7） | 404 |
| 已删商机 | 200，真落库（7 → 8） | 404 |

这轮顺带**纠正**了一句写错的结论：原本以为"挂不存在的客户 id 会变成谁都看不见的
隐形垃圾"，实测是外键当场挡住、以 500 的形式暴露。不跑这一次就不会发现。

## 一个顺带确认的现状（不是本套件要修的）

`GET /logistics/rates` 的签名里**没有 keyword 参数**，它无条件返回全部费率。
所以别处那句"按关键字兜底清理测试费率"实际上清的是**所有**费率 ——
本套件因此自己建费率、按 **id** 删，不依赖那套写法。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      FILE_ROOT=data/iso-files-xxx PYTHONPATH=. .venv/bin/python \\
      scripts/check_tenth_round_logistics.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import String, delete, func, or_, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.pricing.model import LogisticsQuote, LogisticsRate
from app.modules.product.model import Sku
from app.modules.user.model import User

#: ⚠️ 必须**显式**给 API_BASE，不给就拒跑。
#:
#: 本套件会真的落物流试算记录、建客户与费率。别的套件默认打 8000（开发后端），
#: 一旦忘了传 API_BASE，请求就会打到**开发库**：夹具建在隔离库、写入落在开发库。
#: 宁可跑不起来，也别悄悄写错库（`check_pool_wait_and_settings` 同款防呆）。
if not os.environ.get("API_BASE"):
    raise SystemExit(
        "必须显式设置 API_BASE（不能依赖默认的 8000，那是开发后端）：\n"
        "  API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\\n"
        "    PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_logistics.py"
    )
BASE = os.environ["API_BASE"].rstrip("/")

MARKER = f"CHKLOG{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def quote_count() -> int:
    """库里现有的试算记录条数 —— 用来证明"被拒时一条都没落"。"""
    async with SessionLocal() as session:
        return int(
            (await session.execute(select(func.count()).select_from(LogisticsQuote))).scalar_one()
        )


async def quote_customer_id(quote_id: int) -> int | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(LogisticsQuote.customer_id).where(LogisticsQuote.id == quote_id)
            )
        ).scalar_one_or_none()


async def main():
    admin = login("admin", "admin123")
    zhangsan = login("zhangsan", "123456")

    customer_ids: list[int] = []
    opportunity_ids: list[int] = []
    quote_ids: list[int] = []
    rate_ids: list[int] = []

    #: 本条费率用的承运商名。清理要靠它把"真落了库、但没记上账"的试算记录也收掉
    #: （断言失败时就会出现这种记录），所以先给个默认值。
    provider: str | None = None

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        # ------------------------------------------------------------ 夹具
        print("=== 0. 夹具 ===")
        async with SessionLocal() as session:
            zhangsan_id = int(
                (await session.execute(select(User.id).where(User.username == "zhangsan"))).scalar_one()
            )
            lisi_id = int(
                (await session.execute(select(User.id).where(User.username == "lisi"))).scalar_one()
            )
            # 取一个 seed 里活着的 SKU，不新建（本套件测的不是 SKU）
            sku_id = int(
                (
                    await session.execute(
                        select(Sku.id)
                        .where(Sku.deleted_at.is_(None))
                        .order_by(Sku.id.asc())
                        .limit(1)
                    )
                ).scalar_one()
            )
            stage_id = int(
                (
                    await session.execute(
                        select(OpportunityStage.id).order_by(OpportunityStage.id.asc()).limit(1)
                    )
                ).scalar_one()
            )

            mine = Customer(name=f"{MARKER}我的客户", owner_id=zhangsan_id, pool_status="private")
            theirs = Customer(name=f"{MARKER}同事的客户", owner_id=lisi_id, pool_status="private")
            session.add_all([mine, theirs])
            await session.flush()
            # 夹具写库必须 commit：接口是**另一个会话**，不提交它看不到（本项目实测踩过）
            await session.commit()
            customer_ids += [mine.id, theirs.id]
            my_customer_id, their_customer_id = mine.id, theirs.id

            my_opp = Opportunity(
                customer_id=my_customer_id, title=f"{MARKER}我的商机",
                stage_id=stage_id, owner_id=zhangsan_id,
            )
            other_opp = Opportunity(
                customer_id=their_customer_id, title=f"{MARKER}同事的商机",
                stage_id=stage_id, owner_id=lisi_id,
            )
            gone_opp = Opportunity(
                customer_id=my_customer_id, title=f"{MARKER}已删商机",
                stage_id=stage_id, owner_id=zhangsan_id,
                deleted_at=datetime.now(UTC),  # 已删：用来验"软删的商机不许挂"
            )
            session.add_all([my_opp, other_opp, gone_opp])
            await session.flush()
            await session.commit()
            opportunity_ids += [my_opp.id, other_opp.id, gone_opp.id]
            my_opp_id, other_opp_id, gone_opp_id = my_opp.id, other_opp.id, gone_opp.id

        check("夹具：两个客户建出来了", len(customer_ids) == 2, customer_ids)
        check("夹具：三个商机建出来了", len(opportunity_ids) == 3, opportunity_ids)

        rate = api(
            "POST", "/logistics/rates", token=admin,
            body={
                "provider": f"{MARKER}物流",
                "origin_region": "华东",
                "destination_region": f"{MARKER}华北",
                "shipping_method": "陆运",
                "unit_price_per_kg": 1,
                "unit_price_per_volume": 200,
                "min_charge": 0,
            },
        )
        rate_ids.append(rate["id"])
        provider = rate["provider"]
        check("夹具：费率建出来了", bool(rate.get("id")), rate.get("id"))

        #: 固定重量与体积（override 覆盖 SKU 上的值），断言不跟着 SKU 的数据漂
        frozen = {
            "quantity": 10,
            "weight_override": 2,
            "volume_override": 0.5,
            "origin": "华东",
            "destination": f"{MARKER}华北",
            "shipping_method": "陆运",
        }

        def calc(**extra):
            return call(
                "POST", "/logistics/calculate", token=zhangsan,
                body={"sku_id": sku_id, "selected_provider": provider, **frozen, **extra},
            )

        # ---------------------------------------------------- 1. 合法的两种
        print("=== 1. 合法：带自己的客户 / 不带客户，照常落库 ===")
        before = await quote_count()
        status, res = calc(save=True, customer_id=my_customer_id)
        check("带自己的客户 → 可以保存",
              status == 200 and res.get("code") == 0, f"HTTP {status} {res.get('message')}")
        own_quote_id = res.get("data", {}).get("quoted_id")
        check("返回了记录 id", bool(own_quote_id), own_quote_id)
        if own_quote_id:
            quote_ids.append(int(own_quote_id))
            check("落库时记下的就是传进去的那个客户",
                  await quote_customer_id(int(own_quote_id)) == my_customer_id)

        status, res = calc(save=True)
        check("不带客户（纯比价）→ 照常能保存",
              status == 200 and res.get("code") == 0, f"HTTP {status} {res.get('message')}")
        bare_quote_id = res.get("data", {}).get("quoted_id")
        check("不带客户时也返回了记录 id", bool(bare_quote_id), bare_quote_id)
        if bare_quote_id:
            quote_ids.append(int(bare_quote_id))
            check("不带客户时记录里确实没有客户",
                  await quote_customer_id(int(bare_quote_id)) is None)

        after = await quote_count()
        check("两条都真落了库", after - before == 2, f"{before} → {after}")

        # ---------------------------------------------- 2. 同事的客户（越权）
        print("=== 2. 挂「同事的客户」→ 拒绝，且一条都不许落 ===")
        before = await quote_count()
        status, res = calc(save=True, customer_id=their_customer_id)
        check("挂同事的客户被拒（403 数据范围）", status == 403, f"HTTP {status} {res.get('message')}")
        check("拒绝理由说清是数据范围",
              "数据范围" in json.dumps(res, ensure_ascii=False), res.get("message"))
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        # ------------------------------------------ 3. 不存在的客户（隐形垃圾）
        print("=== 3. 挂「不存在的客户编号」→ 拒绝（从前是 500，不是「客户不存在」）===")
        before = await quote_count()
        status, res = calc(save=True, customer_id=999_999_999)
        check("不存在的客户被拒（404）", status == 404, f"HTTP {status} {res.get('message')}")
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        # --------------------------------------------------- 4. 已删的客户
        print("=== 4. 挂「已删客户」→ 拒绝 ===")
        async with SessionLocal() as session:
            doomed = Customer(name=f"{MARKER}将被删的客户", owner_id=zhangsan_id, pool_status="private")
            session.add(doomed)
            await session.flush()
            await session.commit()
            customer_ids.append(doomed.id)
            doomed_id = doomed.id
        api("DELETE", f"/customers/{doomed_id}", token=admin)

        before = await quote_count()
        status, res = calc(save=True, customer_id=doomed_id)
        check("已删客户被拒（404）", status == 404, f"HTTP {status} {res.get('message')}")
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        # --------------------------------------------------------- 5. 商机
        print("=== 5. 商机：对得上才放行，对不上 / 不存在 / 已删一律拦 ===")
        status, res = calc(save=True, customer_id=my_customer_id, opportunity_id=my_opp_id)
        check("客户与商机对得上 → 可以保存",
              status == 200 and res.get("code") == 0, f"HTTP {status} {res.get('message')}")
        paired_id = res.get("data", {}).get("quoted_id")
        if paired_id:
            quote_ids.append(int(paired_id))

        before = await quote_count()
        status, res = calc(save=True, customer_id=my_customer_id, opportunity_id=other_opp_id)
        check("商机不属于该客户 → 422（不许留下自相矛盾的一行）",
              status == 422, f"HTTP {status} {res.get('message')}")
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        before = await quote_count()
        status, res = calc(save=True, opportunity_id=999_999_999)
        check("商机不存在 → 404", status == 404, f"HTTP {status} {res.get('message')}")
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        before = await quote_count()
        status, res = calc(save=True, opportunity_id=gone_opp_id)
        check("已删商机 → 404", status == 404, f"HTTP {status} {res.get('message')}")
        now = await quote_count()
        check("被拒时没有偷偷落一条", now == before, f"{before} → {now}")

        # ------------------------------------------------- 6. 与列表口径一致
        print("=== 6. 口径一致：落库 / 列表 / 详情同一条判据 ===")
        status, _ = call("GET", f"/logistics/quotes/{bare_quote_id}", token=zhangsan)
        check("不带客户的记录：本人看得到详情", status == 200, f"HTTP {status}")

        listed = api(
            "GET", f"/logistics/quotes?customer_id={my_customer_id}&page_size=100", token=zhangsan
        )
        got = {int(row["id"]) for row in listed["items"]}
        want = {int(own_quote_id)}
        if paired_id:
            want.add(int(paired_id))
        check("按自己的客户筛，能筛到刚存的那几条", want <= got, sorted(got))

    finally:
        print("=== 清理 ===")
        async with SessionLocal() as session:
            # ⚠️ 试算记录必须**按范围**删，不能只删"记过账的 id"。
            # 断言失败时（实现坏了、本该被拒的却真落了库）那些记录不会进 `quote_ids`；
            # 漏掉它们，下一步删客户就会撞 `logistics_quotes_customer_id_fkey`、
            # 把整段清理带崩 —— 反向验证时实打实踩过：清理一崩，客户/商机/费率
            # 全留成残渣，接着害到后面的套件。宁可多删几条自己的，也别漏。
            doomed = []
            if provider:
                doomed.append(LogisticsQuote.provider == provider)
            if customer_ids:
                doomed.append(LogisticsQuote.customer_id.in_(customer_ids))
            if opportunity_ids:
                doomed.append(LogisticsQuote.opportunity_id.in_(opportunity_ids))
            if doomed:
                await session.execute(delete(LogisticsQuote).where(or_(*doomed)))
            # 落库会写审计（business_type=logistics_quote）+ 删客户也会写，
            # 统一按"内容里带本套件标记"收掉，别只按 id 收（容易漏）
            await session.execute(
                delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
            )
            await session.execute(
                delete(AuditLog).where(AuditLog.before_data.cast(String).contains(MARKER))
            )
            if opportunity_ids:
                await session.execute(delete(Opportunity).where(Opportunity.id.in_(opportunity_ids)))
            if customer_ids:
                await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            if rate_ids:
                await session.execute(delete(LogisticsRate).where(LogisticsRate.id.in_(rate_ids)))
            # **必须显式提交**：`async with SessionLocal()` 退出只 close，
            # 没提交的事务整体回滚 —— 上面那些 delete 就全白写了。
            await session.commit()
        print(
            f"  已清：试算 {len(quote_ids)} 条、商机 {len(opportunity_ids)} 个、"
            f"客户 {len(customer_ids)} 个、费率 {len(rate_ids)} 条"
        )

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 物流试算落库的客户归属校验（带自己客户 / 不带客户 / 同事客户 / "
        "不存在 / 已删 / 商机对号与不对号）"
    )


if __name__ == "__main__":
    asyncio.run(main())
