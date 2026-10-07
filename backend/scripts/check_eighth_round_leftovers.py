"""前八批返修遗留（§8.14 / §8.7 / §7.3 / §7.6）：把审查给的反例变成断言。

**只在隔离库跑**：API_BASE 必须指向本机，DATABASE_URL 库名必须含 test。

## 这个套件钉住 8 个反例（审查 2026-10-07 复验，逐条实测过的那 8 条）

**§8.14 主数据（4 条）**
1. 只确认一个字段仍能正式发送 —— 发送闸门原来只判 `master_version_no is None`，
   而"只确认名称"也会有版本号。现在回查**明细引用的那一版快照**里有没有
   名称/规格/单位。
2. 明细记的版本号和真实整版快照对不上 —— 原来取"字段确认次数的最大值"，
   与 `sku_master_versions.version_no` 是两套编号。
3. 完全未确认也能生成「有效」的对客 Excel —— 文件入口没接校验。现在落**草稿**。
4. 后端给了 `master_warnings`、前端没接 —— 现在前端接线，且版本详情**常驻**带着它
   （本套件断言后端这一路确实产出，前端靠 tsc + UI 冒烟）。

**§8.7 报价快照（1 条）**
5. 版本编辑入口只改主单、不同步本版快照；且改历史版本会误改主单。

**§7.3 客户专属价（2 条）**
6. 普通 `product:view` 也能拿到最低保护价 —— 现在按 `price:manage` 隐藏。
7. 普通列表仍返回软删客户（和软删 SKU）的规则。

**§7.6 导入预览（1 条）**
8. 旧格式令牌（没有原值指纹）仍被接受 —— 现在"更新行必须有指纹"，否则要求重新预览。

跑法：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_eighth_round_leftovers.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.importing import OUTCOME_CREATED, OUTCOME_UPDATED, diff_preview
from app.modules.product import master as master_svc
from app.modules.user.model import User

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHK8LEFT"
STAMP = str(int(time.time()))


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
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
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def cleanup(ids: dict) -> None:
    async with SessionLocal() as s:
        params = {"p": f"{PREFIX}%", "u": f"{PREFIX.lower()}_%", "r": f"{PREFIX}%"}
        customers = "(select id from customers where name like :p)"
        for sql in (
            "delete from customer_price_rules where customer_id in " + customers,
            f"delete from quote_charges where quote_version_id in (select v.id from quote_versions v "
            f"join quotes q on q.id = v.quote_id where q.customer_id in {customers})",
            f"delete from quote_items where quote_version_id in (select v.id from quote_versions v "
            f"join quotes q on q.id = v.quote_id where q.customer_id in {customers})",
            f"delete from biz_docs where quote_id in (select id from quotes where customer_id in {customers})",
            f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {customers})",
            f"delete from quotes where customer_id in {customers}",
            "delete from customers where name like :p",
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from users where username like :u",
        ):
            await s.execute(text(sql), params)
        if ids.get("sku_id"):
            # 只删**本套件插的**确认记录（seed 的 SKU 是共享的）
            await s.execute(
                text(
                    "delete from sku_master_versions where sku_id = :sku "
                    "and diff_key like :p"
                ),
                {"sku": ids["sku_id"], "p": f"{PREFIX}%"},
            )
            await s.execute(
                text(
                    "delete from sku_field_authorities where sku_id = :sku "
                    "and field_name in ('name', 'specification', 'unit') "
                    "and confirmed_by = :admin"
                ),
                {"sku": ids["sku_id"], "admin": ids.get("admin_id")},
            )
        await s.commit()


async def main() -> None:
    host = urlparse(BASE).hostname
    assert host in ("127.0.0.1", "localhost"), f"只能在本地跑，当前 {BASE}"
    assert "test" in os.environ.get("DATABASE_URL", ""), "只能在隔离库跑"

    admin = login("admin", "admin123")
    ids: dict = {}
    try:
        sku = call("GET", "/pricing/sku-options", admin)[1]["data"][0]["id"]
        ids["sku_id"] = sku

        async with SessionLocal() as s:
            admin_row = await s.get(User, 1)
            ids["admin_id"] = admin_row.id
            ctx = CurrentUser(admin_row, set(), ["admin"], "all")

            # ------------------------------------------------------------------
            print()
            print("=== 8.14-② 版本号必须指向真实整版快照 ===")

            async def confirm(field: str, snapshot_values: dict, version_no: int) -> None:
                """按"人工确认一个字段"的口径插记录：字段权威 + 新出一版整版快照。

                `snapshot_values` 是**这一版整版快照里应有的全部字段值** ——
                快照是全量的，所以第一次确认名称时里面只有名称，第二次加上单位。
                """
                await s.execute(
                    text(
                        "insert into sku_field_authorities "
                        "(sku_id, field_name, confirmed_version, confirmed_value, "
                        " confirmed_by, status, source_verified, created_at, updated_at) "
                        "values (:sku, :f, :v, cast(:val as jsonb), :by, 'confirmed', "
                        " false, now(), now()) "
                        "on conflict (sku_id, field_name) do update set "
                        "confirmed_version = :v, confirmed_value = cast(:val as jsonb), "
                        "status = 'confirmed', confirmed_by = :by, updated_at = now()"
                    ),
                    # JSON 列要传 **JSON 文本**（`json.dumps`），不能传裸字符串
                    {
                        "sku": sku,
                        "f": field,
                        "v": version_no,
                        "val": json.dumps(snapshot_values[field]),
                        "by": admin_row.id,
                    },
                )
                await s.execute(
                    text(
                        "insert into sku_master_versions "
                        "(sku_id, version_no, values, source_summary, confirmed_by, "
                        " confirmed_at, note, diff_key, created_at, updated_at) "
                        "values (:sku, :v, cast(:vals as jsonb), cast(:src as jsonb), "
                        " :by, now(), :note, :key, now(), now()) "
                        "on conflict (sku_id, version_no) do update set "
                        "values = cast(:vals as jsonb), updated_at = now()"
                    ),
                    {
                        "sku": sku,
                        "v": version_no,
                        "vals": json.dumps(snapshot_values),
                        "src": json.dumps({"note": f"{PREFIX} 反例夹具"}),
                        "by": admin_row.id,
                        "note": f"{PREFIX} 第 {version_no} 版",
                        "key": f"{PREFIX}:v{version_no}",
                    },
                )
                await s.commit()

            # 确认名称 → 整版 V1（**只有名称**，没有单位/规格）
            await confirm("name", {"name": "CHK8LEFT 主数据"}, 1)
            only_name = await master_svc.quoted_master_version_no(
                s, sku, {"name": "CHK8LEFT 主数据"}
            )
            check("只确认名称时，引用的整版号是 1", only_name, 1)

            v1_missing = await master_svc.quoted_snapshot_problems(
                s, sku_id=sku, version_no=only_name
            )
            check_true(
                "①只确认名称 → 那一版缺规格与单位（不能正式发送）",
                "规格" in v1_missing and "单位" in v1_missing,
                str(v1_missing),
            )

            # 再确认单位 → 整版 V2（含名称 + 单位）
            await confirm("unit", {"name": "CHK8LEFT 主数据", "unit": "件"}, 2)
            with_unit = await master_svc.quoted_master_version_no(
                s, sku, {"name": "CHK8LEFT 主数据", "unit": "件"}
            )
            check("再确认单位后，引用的整版号变成 2", with_unit, 2)
            check_true(
                "②引用的版本实际包含它采用的值（不再记成 1）",
                with_unit != only_name,
                f"only_name={only_name} with_unit={with_unit}",
            )
            row = (
                await s.execute(
                    text(
                        "select values from sku_master_versions "
                        "where sku_id = :sku and version_no = :v"
                    ),
                    {"sku": sku, "v": with_unit},
                )
            ).scalar_one()
            values = row if isinstance(row, dict) else json.loads(row)
            check("那一版快照里确实有 unit", values.get("unit"), "件")

            # ------------------------------------------------------------------
            print()
            print("=== 8.14-① / ③ / ④ 报价链路上的三处 ===")
            _, created = call("POST", "/quotes", admin, body={"opportunity_id": 1})
            check("建报价草稿", created.get("code"), 0)
            version_id = created["data"]["version_id"]
            ids["version_id"] = version_id

            # ① 发送闸门：不能只看"有没有版本号"
            try:
                from app.modules.quote import service as quote_service

                await quote_service.ensure_items_master_confirmed(s, version_id=version_id)
                check_true("③未确认的明细不能正式发送", False, "居然放行了")
            except AppError as exc:
                check(
                    "③未确认的明细不能正式发送（STATUS_NOT_ALLOWED）",
                    exc.code,
                    ErrorCode.STATUS_NOT_ALLOWED,
                )
            await s.rollback()

            # ③ 文件入口：未确认 → 落草稿（不是"有效"）
            from app.modules.bizdoc import service as bizdoc_service

            doc = await bizdoc_service.generate_quote_doc(
                s, quote_version_id=version_id, user=ctx
            )
            check("④未确认时生成的对客文件是草稿", doc.status, "draft")
            check_true(
                "④草稿的标题也标出来了",
                (doc.title or "").startswith("【草稿】"),
                doc.title or "",
            )
            await s.rollback()

            # ④ 后端把提醒放进响应体（前端据此常驻显示）
            _, detail = call("GET", f"/quote-versions/{version_id}", admin)
            warnings = detail["data"].get("master_warnings")
            check_true(
                "⑤版本详情带着未确认清单（前端能看到、刷新还在）",
                isinstance(warnings, list) and len(warnings) > 0,
                str(warnings),
            )

            # ------------------------------------------------------------------
            print()
            print("=== 8.7 版本编辑入口同步本版快照 ===")
            target = "2027-01-01"
            status, res = call(
                "PATCH", f"/quote-versions/{version_id}", admin,
                body={"valid_until": target},
            )
            check("版本入口改有效期", res.get("code"), 0)
            _, after = call("GET", f"/quote-versions/{version_id}", admin)
            check(
                "⑥版本快照跟着改了（PDF/BizDoc 读的就是它）",
                str(after["data"]["version"]["valid_until_snapshot"])[:10],
                target,
            )
            check(
                "⑥主单也一致（当前版本）",
                str(after["data"]["quote"]["valid_until"])[:10],
                target,
            )
            await s.rollback()

            # ------------------------------------------------------------------
            print()
            print("=== 7.3 专属价：保护价脱敏 + 软删引用 ===")
            from app.modules.pricing import service as pricing_service

            _, cust = call(
                "POST", "/customers", admin, body={"name": f"{PREFIX}客户-{STAMP}", "level": "A"}
            )
            customer_id = cust["data"]["id"]
            ids["customer_id"] = customer_id
            call(
                "POST", "/customer-price-rules", admin,
                body={
                    "customer_id": customer_id, "sku_id": sku,
                    "min_qty": 1, "agreed_price": 150, "minimum_price": 200,
                },
            )
            status, listing = call("GET", f"/customer-price-rules?customer_id={customer_id}", admin)
            rule = (listing["data"]["items"] or [None])[0]
            check_true("⑦管理员能看到保护价（有 price:manage）", rule and rule["minimum_price"] == 200, str(rule))

            # 用核心判据直接验"没有 price:manage 就看不到"（权限矩阵走 HTTP 太重）
            from app.modules.pricing.model import CustomerPriceRule

            rule_row = await s.get(CustomerPriceRule, rule["id"])
            hidden = pricing_service.serialize_customer_price(
                rule_row, "SKU", "客户", can_see_cost=False
            )
            check("⑦无 price:manage 时保护价被隐藏", hidden["minimum_price"], None)
            check("⑦但谈定价保留（销售查价要用）", hidden["agreed_price"], 150.0)

            # 软删客户 → 列表（含总数）都不能再出现
            await s.execute(
                text("update customers set deleted_at = now() where id = :c"), {"c": customer_id}
            )
            await s.commit()
            status, listing = call("GET", "/customer-price-rules", admin)
            hit = [
                item for item in listing["data"]["items"]
                if item["customer_id"] == customer_id
            ]
            check("⑧软删客户的规则不再出现在列表", hit, [])
            await s.rollback()

            # ------------------------------------------------------------------
            print()
            print("=== 7.6 导入预览：旧令牌不能静默降级 ===")
            old = diff_preview(
                {"2": OUTCOME_UPDATED},
                {"2": OUTCOME_UPDATED},
                file_sha256="sha",
                planned_file="sha",
                planned_baselines={},        # 旧格式令牌：没有原值指纹
                actual_baselines={"2": "fp-200"},
            )
            check("⑨旧令牌的更新行被标成缺指纹", old["missing_baseline"], [2])
            fresh = diff_preview(
                {"2": OUTCOME_UPDATED},
                {"2": OUTCOME_UPDATED},
                file_sha256="sha",
                planned_file="sha",
                planned_baselines={"2": "fp-100"},
                actual_baselines={"2": "fp-200"},
            )
            check("⑨新令牌能发现原值被改过", [item["row"] for item in fresh["baseline_changed"]], [2])
            created_only = diff_preview(
                {"3": OUTCOME_CREATED},
                {"3": OUTCOME_CREATED},
                file_sha256="sha",
                planned_file="sha",
                planned_baselines={},
                actual_baselines={},
            )
            check(
                "⑨新增行不要求指纹（与更新行区分开）",
                created_only["missing_baseline"],
                [],
            )
    finally:
        await cleanup(ids)

    print()
    if FAILURES:
        print(f"❌ 前八批遗留回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 前八批遗留回归全部通过")


if __name__ == "__main__":
    asyncio.run(main())
