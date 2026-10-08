"""运费费率的「改」与「删」（价格中心「运费费率」）+ 匹配降级必须说出来。

**只在隔离库跑**：必须**显式**给 `API_BASE`（默认的 8000 是开发后端）与一次性库的
`DATABASE_URL`。本套件会真建费率、真改、真删，还会计较"别人的费率有没有被动过"。

## 为什么要有这个套件（2026-10-08 复审）

那页此前**只能新增**：后端只有查/增/删三个端点，前端连删除都没接（表上连"操作"列
都没有）。承运方式写错、目的地漏填、单价填反，都只能"删掉重建" —— 而重建会换掉 id，
审计里断成两段，看不出是同一条费率的修改。新增弹窗也只收四个字段、运输方式写死
「陆运」，配不出"这家到华东、按方计价"。

同一轮还揭出一件**会漏钱**的事：核价估算取"匹配到的方案里最便宜那条"，而匹配是
**逐级放宽**的（目的地/运输方式对不上 → 放宽 → 最后列出全部启用费率）。一旦放宽，
最便宜那条很可能根本不属于这次要发的地方 —— 运费被算少、毛利被算高，本来该触发
低价审批的报价就不触发了。所以"这次用的不是精确匹配的费率"必须出现在核价提示里。

## 钉住这些

1. `PATCH /logistics/rates/{id}`：**只传要改的字段**，没传的保持原值
2. 完整字段都能改（起运地/目的地/运输方式/两种单价/最低收费/两段时效/状态/备注），读回来一致
3. 两个地区字段**传 `null` 能清空**（= 不限；这与写「全国」**不是一回事**）
4. 库里非空的列传 `null` → **400（40001）且原数据一个字没动**（从前会撞非空约束报 500）
5. 权限：改/删/增都要 `price:manage`，业务员 403；**看列表只要 `product:view`**（业务员 200）
6. 改/删不存在的 → 404；承运方式超长 → 400（不是 500）
7. **停用**（`status=inactive`）之后：不再出现在承运商列表、核价也不再匹配到它
8. **匹配降级必须在核价提示里说清**：命中兜底级时提示里出现「兜底匹配」；
   对照：精确命中时**不该**出现这句话
9. 删除真的删掉了，而且**别人的费率一条没动**

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test_a \\
      PYTHONPATH=. .venv/bin/python scripts/check_logistics_rate_admin.py
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

BASE = (os.environ.get("API_BASE") or "").rstrip("/")
PREFIX = "CHKRATE" + uuid4().hex[:6]
FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def require_isolated_db() -> str:
    """显式要求一次性隔离库：本套件会真的建/改/删费率。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit(
            "必须显式设置 API_BASE（默认的 8000 是开发后端）。"
            "例：API_BASE=http://127.0.0.1:8001/api/v1"
        )
    return name


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
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def cleanup() -> None:
    """清干净本套件写下的东西（费率 / 审计 / 自建的产品与 SKU）。

    审计必须显式删（`logistics_rate` 的留痕按 `business_id` 反查，费率行删了就再也
    找不着它们）。SKU 要排在产品之前（子表先删）。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        for sql in (
            "delete from logistics_rates where provider like :p",
            "delete from audit_logs where business_type = 'logistics_rate'"
            " and (coalesce(before_data::text, '') like :m or coalesce(after_data::text, '') like :m)",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
        ):
            await session.execute(text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%"})
        await session.commit()


def rate_rows(token: str) -> list[dict]:
    return call("GET", "/logistics/rates", token=token)[1].get("data") or []


def find_rate(token: str, rate_id: int) -> dict:
    return next((row for row in rate_rows(token) if row.get("id") == rate_id), {})


def provider_names(token: str) -> list[str]:
    return [
        row.get("provider")
        for row in (call("GET", "/logistics/providers", token=token)[1].get("data") or [])
    ]


async def main() -> int:
    db_name = require_isolated_db()
    print(f"隔离库：{db_name}")
    await cleanup()

    admin = login("admin", "admin123")
    sales = login("zhangsan", "123456")

    # 别人（种子与其它套件）的费率 id —— 最后要比对"一条都没动"
    foreign_before = sorted(
        row["id"] for row in rate_rows(admin)
        if not str(row.get("provider") or "").startswith(PREFIX)
    )

    # ---- 1. 建一条费率（完整字段），顺便拿到一个自建的 SKU 用于核价 ----
    print("\n=== 1. 新增：完整字段都收得下 ===")
    status, res = call("POST", "/logistics/rates", token=admin, body={
        "provider": f"{PREFIX}承运A",
        "origin_region": f"{PREFIX}华东",
        "destination_region": f"{PREFIX}华北",
        "shipping_method": f"{PREFIX}陆运",
        "unit_price_per_kg": 1.2,
        "unit_price_per_volume": 200,
        "min_charge": 30,
        "eta_days": 3,
        "eta_days_max": 5,
        "remark": f"{PREFIX}备注",
    })
    check("建费率", res.get("code"), 0)
    rate_a = (res.get("data") or {}).get("id")
    check_true("拿到了 id", isinstance(rate_a, int), repr(rate_a))
    row = find_rate(admin, rate_a)
    check("起运地存下来了（从前界面填不了）", row.get("origin_region"), f"{PREFIX}华东")
    check("目的地存下来了", row.get("destination_region"), f"{PREFIX}华北")
    check("运输方式没被写死成陆运", row.get("shipping_method"), f"{PREFIX}陆运")
    check("体积单价存下来了（抛货用得着）", row.get("unit_price_per_volume"), 200.0)
    check("时效上限存下来了", row.get("eta_days_max"), 5)
    check("备注存下来了", row.get("remark"), f"{PREFIX}备注")

    # ---- 2. 只传一个字段：别的必须保持原值 ----
    print("\n=== 2. 改：只传要改的字段，没传的保持原值 ===")
    status, res = call("PATCH", f"/logistics/rates/{rate_a}", token=admin,
                       body={"unit_price_per_kg": 2.5})
    check("只改公斤单价", res.get("code"), 0)
    row = find_rate(admin, rate_a)
    check("公斤单价改成了 2.5", row.get("unit_price_per_kg"), 2.5)
    check("起运地没被顺手清掉", row.get("origin_region"), f"{PREFIX}华东")
    check("目的地没被顺手清掉", row.get("destination_region"), f"{PREFIX}华北")
    check("运输方式没被顺手改掉", row.get("shipping_method"), f"{PREFIX}陆运")
    check("最低收费没被顺手清成 0", row.get("min_charge"), 30.0)
    check("时效（起）没变", row.get("eta_days"), 3)
    check("时效（止）没变", row.get("eta_days_max"), 5)
    check("备注没变", row.get("remark"), f"{PREFIX}备注")
    check("状态没变", row.get("status"), "active")

    # ---- 3. 全字段一起改 ----
    print("\n=== 3. 改：全字段一起改 ===")
    status, res = call("PATCH", f"/logistics/rates/{rate_a}", token=admin, body={
        "provider": f"{PREFIX}承运A改",
        "origin_region": f"{PREFIX}华南",
        "destination_region": f"{PREFIX}西南",
        "shipping_method": f"{PREFIX}空运",
        "unit_price_per_kg": 3.5,
        "unit_price_per_volume": 180,
        "min_charge": 80,
        "eta_days": 1,
        "eta_days_max": 2,
        "status": "inactive",
        "remark": f"{PREFIX}备注2",
    })
    check("全字段一起改", res.get("code"), 0)
    row = find_rate(admin, rate_a)
    check("承运方式改了", row.get("provider"), f"{PREFIX}承运A改")
    check("运输方式改了", row.get("shipping_method"), f"{PREFIX}空运")
    check("状态改成已停用", row.get("status"), "inactive")
    check("体积单价改了", row.get("unit_price_per_volume"), 180.0)

    # ---- 4. 地区字段传 null = 清空（不限）；与写「全国」不是一回事 ----
    print("\n=== 4. 两个地区字段可以清空（= 不限） ===")
    status, res = call("PATCH", f"/logistics/rates/{rate_a}", token=admin,
                       body={"origin_region": None, "destination_region": None})
    check("清空地区", res.get("code"), 0)
    row = find_rate(admin, rate_a)
    check("起运地清空了", row.get("origin_region"), None)
    check("目的地清空了", row.get("destination_region"), None)

    # ---- 5. 库里非空的列传 null → 400 且原数据一个字没动 ----
    print("\n=== 5. 必填字段传 null：400（不是 500），且原数据没动 ===")
    before = find_rate(admin, rate_a)
    for field, label in (
        ("provider", "承运方式"),
        ("shipping_method", "运输方式"),
        ("unit_price_per_kg", "公斤单价"),
        ("min_charge", "最低收费"),
        ("status", "状态"),
    ):
        status, res = call("PATCH", f"/logistics/rates/{rate_a}", token=admin,
                           body={field: None})
        check(f"{label}传 null → 400", status, 400)
        check(f"{label}传 null 的错误码是参数错误", res.get("code"), 40001)
    check("五次被拒之后那一行**一个字都没动**", find_rate(admin, rate_a), before)

    # ---- 6. 超长 / 不存在 ----
    print("\n=== 6. 超长与不存在 ===")
    status, res = call("PATCH", f"/logistics/rates/{rate_a}", token=admin,
                       body={"provider": "X" * 80})
    check("承运方式超长 → 400（不是 500）", status, 400)
    check("不存在的费率改 → 404",
          call("PATCH", "/logistics/rates/99999999", token=admin,
               body={"min_charge": 1})[0], 404)
    check("不存在的费率删 → 404",
          call("DELETE", "/logistics/rates/99999999", token=admin)[0], 404)

    # ---- 7. 权限：改/删/增要 price:manage，看只要 product:view ----
    print("\n=== 7. 权限门槛 ===")
    check("业务员能看费率列表（product:view）", call("GET", "/logistics/rates", token=sales)[0], 200)
    check("业务员改费率 → 403",
          call("PATCH", f"/logistics/rates/{rate_a}", token=sales,
               body={"min_charge": 1})[0], 403)
    check("业务员删费率 → 403",
          call("DELETE", f"/logistics/rates/{rate_a}", token=sales)[0], 403)
    check("业务员增费率 → 403",
          call("POST", "/logistics/rates", token=sales,
               body={"provider": f"{PREFIX}业务员建的"})[0], 403)
    check("被拒之后那一行仍然没动", find_rate(admin, rate_a), before)

    # ---- 8. 停用之后：承运商列表与核价都不再认它 ----
    print("\n=== 8. 停用 = 退出核价匹配（数据留着） ===")
    # 自建一个带单重的 SKU：核价要靠它算运费（种子 SKU 不一定有单重）
    _, res = call("POST", "/products", token=admin, body={"name": f"{PREFIX}产品"})
    product_id = (res.get("data") or {}).get("id")
    check_true("建了个自用产品", isinstance(product_id, int), repr(res.get("message")))
    _, res = call("POST", f"/products/{product_id}/skus", token=admin, body={
        "sku_code": f"{PREFIX}-S1", "name": f"{PREFIX}SKU", "weight": 2,
    })
    sku_id = (res.get("data") or {}).get("id")
    check_true("建了个带单重的 SKU（2kg）", isinstance(sku_id, int), repr(res.get("message")))

    # 一条**独一无二**的运输方式：只有它匹配得上，且 0.1 元/kg 便宜到必然胜出
    unique_method = f"{PREFIX}空运"
    _, res = call("POST", "/logistics/rates", token=admin, body={
        "provider": f"{PREFIX}停用测试",
        "origin_region": None,
        "destination_region": None,
        "shipping_method": unique_method,
        "unit_price_per_kg": 0.1,
        "min_charge": 0,
    })
    rate_b = (res.get("data") or {}).get("id")
    check_true("建了一条只按运输方式匹配的费率", isinstance(rate_b, int), repr(res))

    def estimate(destination=None, method=None):
        """试算一次，返回 (命中级别, 方案里的承运商, 提示)。"""
        body = {"sku_id": sku_id, "quantity": 10}
        if destination:
            body["destination"] = destination
        if method:
            body["shipping_method"] = method
        _, res = call("POST", "/logistics/calculate", token=admin, body=body)
        data = res.get("data") or {}
        return (
            data.get("match_level"),
            [option.get("provider") for option in (data.get("options") or [])],
            data.get("warnings") or [],
        )

    level, providers, _warnings = estimate(method=unique_method)
    check("启用时：精确命中（第 1 级）", level, 1)
    check_true("启用时：它就在方案里", f"{PREFIX}停用测试" in providers, str(providers))
    check_true("启用时：承运商列表里有它", f"{PREFIX}停用测试" in provider_names(admin))

    check("把它停用（不用删）",
          call("PATCH", f"/logistics/rates/{rate_b}", token=admin,
               body={"status": "inactive"})[0], 200)
    level, providers, _warnings = estimate(method=unique_method)
    check_true("停用后：不再出现在承运商列表里（试算页的下拉跟着干净）",
               f"{PREFIX}停用测试" not in provider_names(admin))
    check_true("停用后：核价也不再匹配到它", f"{PREFIX}停用测试" not in providers,
               str(providers))
    check_true("停用后：匹配降级了（不该再报成第 1 级）", (level or 0) >= 2, str(level))

    # ---- 9. 匹配降级必须在核价提示里说清 ----
    print("\n=== 9. 兜底费率必须说出来（否则运费算少看不出来） ===")
    # 用一个**对不上任何费率**的运输方式、且不给目的地 → 一路放宽到底（第 5 级兜底）
    level, _providers, warnings = estimate(method=f"{PREFIX}谁也不匹配的方式")
    check("兜底级命中", level, 5)
    # 试算这一侧的提示本来就说清了"放宽成了什么"（页面还会把全部方案列出来）；
    # 「可能不准、请手工核对」那句按口径加在**核价**提示里（见下面）。
    check_true("试算的提示说清了放宽成什么",
               any("列出全部启用中的费率" in str(w) for w in warnings), str(warnings))

    # 核价接口：同一条链路都要带上这句话（页面上看得到的那一处）。
    # 这里**刻意不带 `quoted_price`**（那字段在接口里是可选的）—— 顺带盖住
    # 2026-10-08 发现的那个 500：没成本 + 没价格规则 + 有费率时，
    # `check_price` 是 None，折扣判定那句 `check_price < standard_price` 直接抛
    # TypeError（带报价金额的老用例覆盖不到）。
    status, res = call("POST", "/pricing/calculate", token=admin, body={
        "sku_id": sku_id, "quantity": 10,
        "shipping_method": f"{PREFIX}谁也不匹配的方式",
    })
    check("核价不带报价金额 → 200（从前是 500）", status, 200)
    pricing_warnings = (res.get("data") or {}).get("warnings") or []
    check_true("核价结果的提示里也点出了「兜底匹配」",
               any("兜底匹配" in str(w) for w in pricing_warnings), str(pricing_warnings))

    # 对照：精确命中时**不该**出现这句话（否则就是狼来了，用户会忽略它）
    check("把那条费率改回启用",
          call("PATCH", f"/logistics/rates/{rate_b}", token=admin,
               body={"status": "active"})[0], 200)
    level, _providers, warnings = estimate(method=unique_method)
    check("对照：这次是精确命中", level, 1)
    check_true("对照：提示里**没有**「兜底匹配」这句话",
               not any("兜底匹配" in str(w) for w in warnings), str(warnings))
    status, res = call("POST", "/pricing/calculate", token=admin, body={
        "sku_id": sku_id, "quantity": 10, "shipping_method": unique_method,
    })
    # 先确认这一调**真的成功**：失败时 warnings 也是空数组，
    # 下面那句"没有兜底字样"会**假绿**（本项目踩过这个形态）。
    check("对照：核价本身成功", status, 200)
    pricing_warnings = (res.get("data") or {}).get("warnings") or []
    check_true("对照：核价提示里也没有这句话",
               not any("兜底匹配" in str(w) for w in pricing_warnings), str(pricing_warnings))

    # ---- 10. 修改写了审计（不是静默改） ----
    print("\n=== 10. 改与删都留痕 ===")
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        actions = [
            row[0] for row in (
                await session.execute(
                    text(
                        "select action from audit_logs where business_type = 'logistics_rate'"
                        " and business_id = :b order by id"
                    ),
                    {"b": rate_a},
                )
            ).all()
        ]
    check("这条费率的留痕依次是 建 → 改 → 改 → 改（不是只有一条 create）",
          actions[:1], ["create"])
    check_true("改过几次就留几条 update", actions.count("update") >= 3, str(actions))

    # ---- 11. 删除：真删掉了；别人的一条没动 ----
    print("\n=== 11. 删除 ===")
    check("删掉自建的那条", call("DELETE", f"/logistics/rates/{rate_a}", token=admin)[0], 200)
    check("列表里没有它了", find_rate(admin, rate_a), {})
    check("再删一次 → 404", call("DELETE", f"/logistics/rates/{rate_a}", token=admin)[0], 404)
    check("别人的费率一条没动", sorted(
        row["id"] for row in rate_rows(admin)
        if not str(row.get("provider") or "").startswith(PREFIX)
    ), foreign_before)

    await cleanup()

    print()
    if FAILURES:
        print(f"❌ 运费费率维护回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 运费费率维护回归通过")
    return 0


if __name__ == "__main__":
    import app.main  # noqa: F401  触发模型注册（不启调度器）

    _ = app.main
    asyncio.run(main())
