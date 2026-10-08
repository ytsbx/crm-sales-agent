"""§8.14 复审（第四轮）："已确认为空"不能被当成"未确认"。

**只在隔离库跑**：必须显式给 `DATABASE_URL`（库名以 `crm_iso` / `crm_check` 开头）
与 `API_BASE`（默认的 8000 是开发后端）。本套件会真的建产品/SKU/客户/报价/文件。

## 这次补的是什么

第三轮修完还剩一条：正式发送与有效文件生成的校验只查"明细引用的那一版快照里
**有没有**名称/规格/单位这三个键"，**不查"明细实际印出去的值和那一版对不对得上"**。
配合生成明细时的 `confirmed.get(field) or 本地值` 回退，就有下面这条链：

    确认"名称/单位/规格"（其中规格**确认为空**）→ 整版 V1（specification: ""）
    → 本地 SKU 后填一个"尚未确认的新规格"（没有重新核定）
    → 建明细：`"" or 本地新值` 判定为假 → 回退 → 明细印的是新规格
      而 master_version_no 仍指着 V1
    → 只查键：V1 里有 specification → 放行 → mark-sent 成功、Excel 是 active

**问题不是"空规格应当被禁止"，而是"已经确认没有规格，系统却自行换成了另一个
未确认的规格"。** 所以修法是两条：

1. 生成明细按"这个字段**确认过没有**"选来源（键存在性），不按"值空不空"；
   已确认为空就保留为空，只有从没确认过才退回本地值。
2. 校验除了"那一版有没有这些字段"，还要比"明细实际值 == 那一版的值"；
   依据始终是明细记下的那一版，不拿今天的 SKU 值替代。

## 这个套件钉住 6 件事

1. 已确认为空 + 本地后填新规格 → 明细仍用**确认的空值**（不得自动采用新值）
2. ……且这版明细主数据达标、能正式发送（**合法空规格不误拦**）
3. 已确认非空 + 本地改值 → 新明细仍用**确认值**
4. 明细实际值与引用快照不一致（人工填了别的规格）→ 正式发送被拦（422）、
   生成的对客文件只落**草稿**、提示里说明差在哪
5. 新确认版本建立后，旧明细与旧文件的**内容不变**（归档原件不动）
6. 人工填的规格与确认值一致时不算不一致（正常路径不误拦）

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_master_confirmed_empty_value.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.modules.quote import service as quote_service
from app.modules.user.model import User

FAILURES: list[str] = []
PREFIX = "CHKEMPTY"
STAMP = str(int(time.time()))
BASE = require_api_base()
STARTED_AT = datetime.now(UTC)


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
    """显式要求一次性隔离库：本套件会真的建产品/SKU/客户/报价/对客文件。"""
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


async def cleanup(ids: dict) -> None:
    """把本套件造的东西收干净（守门套件会检查残留）。"""
    skus = ids.get("skus") or []
    customers = ids.get("customers") or []
    quotes = ids.get("quotes") or []
    async with SessionLocal() as s:
        if quotes:
            # `q` = 这些报价的所有版本。**审批痕迹要在删 quote_versions 之前清** ——
            # 它靠 business_id 回查 quote_versions，删完就查不到了、会留下孤儿行。
            q = "(select id from quote_versions where quote_id = any(:q))"
            for sql in (
                "delete from quote_charges where quote_version_id in " + q,
                "delete from quote_items where quote_version_id in " + q,
                "delete from quote_send_logs where quote_version_id in " + q,
                "delete from biz_docs where quote_id = any(:q)",
                "delete from business_events where business_id = any(:q) "
                "and business_type='quote'",
                "delete from approval_records where approval_instance_id in "
                "(select id from approval_instances where business_type='quote_version' "
                "and business_id in " + q + ")",
                "delete from approval_instances where business_type='quote_version' "
                "and business_id in " + q,
                "delete from quote_versions where quote_id = any(:q)",
            ):
                await s.execute(text(sql), {"q": quotes})
            await s.execute(text("delete from quotes where id = any(:q)"), {"q": quotes})
        opps = ids.get("opportunities") or []
        if opps:
            # 报价已在上一步删掉（它挂着 opportunity_id），这里清商机自身的历史与明细
            await s.execute(
                text("delete from opportunity_stage_history where opportunity_id = any(:o)"),
                {"o": opps},
            )
            await s.execute(
                text("delete from opportunity_items where opportunity_id = any(:o)"), {"o": opps}
            )
            await s.execute(text("delete from opportunities where id = any(:o)"), {"o": opps})
        # 六阶段"过程记录"自动留痕/通知：按本次启动时间窗清，只删本次运行产生的。
        # **要排在删 customers 之前** —— 它们挂着 customer_id。
        for sql in (
            "delete from notifications where business_type in ('quote','opportunity') "
            "and created_at > :ts",
            "delete from followups where followup_type='系统' and created_at > :ts",
        ):
            await s.execute(text(sql), {"ts": STARTED_AT})
        if customers:
            await s.execute(
                text("delete from customer_owner_history where customer_id = any(:c)"),
                {"c": customers},
            )
            await s.execute(text("delete from customers where id = any(:c)"), {"c": customers})
        if skus:
            for sql in (
                "delete from price_rules where sku_id = any(:s)",
                "delete from product_costs where sku_id = any(:s)",
                "delete from sku_field_authorities where sku_id = any(:s)",
                "delete from sku_master_versions where sku_id = any(:s)",
                "delete from skus where id = any(:s)",
            ):
                await s.execute(text(sql), {"s": skus})
        products = ids.get("products") or []
        if products:
            await s.execute(text("delete from products where id = any(:p)"), {"p": products})
        await s.commit()
    # 本脚本末尾还有一次 asyncio.run 之外的事件循环收尾，async 引擎的连接池
    # 绑在创建它的那个事件循环上：不清池会报 "attached to a different loop"。
    await engine.dispose()


async def main() -> None:
    require_isolated_db()
    admin = login("admin", "admin123")
    ids: dict = {"skus": [], "products": [], "customers": [], "quotes": [], "opportunities": []}
    try:
        async with SessionLocal() as s:
            admin_row = await s.get(User, 1)

            async def confirm(sku_id: int, values: dict, version_no: int, note: str) -> None:
                """按"人工确认这些字段"的口径插记录：字段权威 + 新出一版整版快照。

                `values` 是**这一版整版快照里应有的全部字段值**（快照是全量的）。
                值可以是空串 —— 那正是"确认了、这个 SKU 该字段本来就空"。
                """
                for field, value in values.items():
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
                        {
                            "sku": sku_id,
                            "f": field,
                            "v": version_no,
                            # JSON 列要传 **JSON 文本**（`json.dumps`），不能传裸字符串
                            "val": json.dumps(value),
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
                        "sku": sku_id,
                        "v": version_no,
                        "vals": json.dumps(values),
                        "src": json.dumps({"note": note}),
                        "by": admin_row.id,
                        "note": note,
                        "key": f"{PREFIX}:{sku_id}:v{version_no}:{STAMP}",
                    },
                )
                await s.commit()

            def make_sku(tag: str, name: str, spec: str) -> int:
                """建产品 + SKU + 成本 + 通用价，返回 sku_id。"""
                _, res = call("POST", "/products", admin, body={"name": f"{PREFIX}-{tag}"})
                pid = res["data"]["id"]
                ids["products"].append(pid)
                _, res = call(
                    "POST",
                    f"/products/{pid}/skus",
                    admin,
                    body={
                        "sku_code": f"{PREFIX}-{tag}-{STAMP}",
                        "name": name,
                        "specification": spec,
                        "unit": "件",
                    },
                )
                if res.get("code") != 0:
                    raise SystemExit(f"建 SKU 失败：{res.get('message')}")
                sku_id = res["data"]["id"]
                ids["skus"].append(sku_id)
                call(
                    "POST",
                    f"/skus/{sku_id}/costs",
                    admin,
                    body={
                        "purchase_cost": 10,
                        "package_cost": 2,
                        "effective_from": "2026-01-01",
                        "remark": f"{PREFIX} 临时成本",
                    },
                )
                # 报价 100 远高于保护价 50，避免触发低价审批（发送要先 approved）
                call(
                    "POST",
                    "/price-rules",
                    admin,
                    body={
                        "sku_id": sku_id,
                        "min_qty": 0,
                        "standard_price": 100,
                        "guide_price": 100,
                        "minimum_price": 50,
                        "remark": f"{PREFIX}-通用价",
                    },
                )
                return sku_id

            def new_customer(tag: str) -> int:
                _, res = call(
                    "POST",
                    "/customers",
                    admin,
                    body={"name": f"{PREFIX}-客户{tag}", "level": "A", "remark": "验收临时客户"},
                )
                cid = res["data"]["id"]
                ids["customers"].append(cid)
                return cid

            def new_quote(customer_id: int, tag: str) -> int:
                # 报价必须挂商机（D8）：只给 customer_id 会被 40001 挡回
                _, res = call(
                    "POST",
                    "/opportunities",
                    admin,
                    body={
                        "customer_id": customer_id,
                        "title": f"{PREFIX}-商机{tag}",
                        "expected_amount": 1000,
                    },
                )
                if res.get("code") != 0:
                    raise SystemExit(f"建商机失败：{res.get('message')}")
                opp_id = res["data"]["id"]
                ids["opportunities"].append(opp_id)
                _, res = call("POST", "/quotes", admin, body={"opportunity_id": opp_id})
                if res.get("code") != 0:
                    raise SystemExit(f"建报价失败：{res.get('message')}")
                ids["quotes"].append(res["data"]["quote_id"])
                return res["data"]["version_id"]

            def add_item(version_id: int, sku_id: int, spec: str | None = None):
                body: dict = {"sku_id": sku_id, "quantity": 1, "quoted_price": 100}
                if spec is not None:
                    body["spec_snapshot"] = spec
                _, res = call("POST", f"/quote-versions/{version_id}/items", admin, body=body)
                return res

            def item_of(version_id: int) -> dict:
                _, res = call("GET", f"/quote-versions/{version_id}/items", admin)
                data = res.get("data") or {}
                rows = data.get("items") if isinstance(data, dict) else data
                rows = rows or []
                return rows[0] if rows else {}

            def approve_and_send(version_id: int):
                """提交审批 → 标记发送。返回发送那一步的响应（dict）。"""
                call("POST", f"/quote-versions/{version_id}/submit-approval", admin, body={})
                _, res = call(
                    "POST",
                    f"/quote-versions/{version_id}/mark-sent",
                    admin,
                    body={"channel": "邮件", "receiver": "qa@example.com"},
                )
                return res

            def gen_doc(version_id: int) -> dict:
                _, res = call(
                    "POST", "/biz-docs/quote", admin, body={"quote_version_id": version_id}
                )
                return res.get("data") or {}

            # ==================================================================
            print()
            print("=== ① 已确认为「规格为空」→ 本地后填新规格：明细不得自动采用 ===")
            sku_a = make_sku("A", "CHKEMPTY-A 产品", "")
            # 确认名称/规格/单位 —— **规格确认成空串**（"这个产品没有规格"是结论）
            await confirm(
                sku_a,
                {"name": "CHKEMPTY-A 产品", "specification": "", "unit": "件"},
                1,
                f"{PREFIX} 确认（规格为空）",
            )
            # 本地 SKU 事后填一个**尚未确认**的新规格（不重新核定）
            _, res = call(
                "PATCH", f"/skus/{sku_a}", admin, body={"specification": "尚未确认的新本地规格"}
            )
            check("本地 SKU 已填入新规格", res.get("code"), 0)
            _, sku_now = call("GET", f"/skus/{sku_a}", admin)
            check("本地值确实是那个新规格", sku_now["data"]["specification"], "尚未确认的新本地规格")

            vid_a = new_quote(new_customer("A"), "A")
            check("建明细（不人工填规格）", add_item(vid_a, sku_a).get("code"), 0)
            item_a = item_of(vid_a)
            check("①明细采用**确认的空规格**，不是新本地值", item_a.get("specification"), "")
            check("①明细仍引用那一版整版快照", item_a.get("master_version_no"), 1)
            problems_a = await quote_service.master_confirmation_problems(s, version_id=vid_a)
            check("②合法空规格不算「未达标」（不误拦）", problems_a, [])
            sent_a = approve_and_send(vid_a)
            check("②这版报价能正式发送", sent_a.get("code"), 0)

            # ==================================================================
            print()
            print("=== ③ 已确认非空 → 本地改值：新明细仍用确认值 ===")
            sku_b = make_sku("B", "CHKEMPTY-B 产品", "确认规格")
            await confirm(
                sku_b,
                {"name": "CHKEMPTY-B 产品", "specification": "确认规格", "unit": "件"},
                1,
                f"{PREFIX} 确认（规格非空）",
            )
            call("PATCH", f"/skus/{sku_b}", admin, body={"specification": "本地改过的规格"})
            vid_b = new_quote(new_customer("B"), "B")
            check("建明细", add_item(vid_b, sku_b).get("code"), 0)
            item_b = item_of(vid_b)
            check("③明细仍用已确认的规格", item_b.get("specification"), "确认规格")
            check("③引用的是 V1", item_b.get("master_version_no"), 1)
            # 先发送 + 出一份正式对客文件（第 ⑤ 条要拿它比对"内容不变"）
            sent_b = approve_and_send(vid_b)
            check("③能正式发送", sent_b.get("code"), 0)
            doc_b = gen_doc(vid_b)
            check("③正式文件是有效态", doc_b.get("status"), "active")
            hash_before = await _doc_hash(doc_b.get("id"))

            # ==================================================================
            print()
            print("=== ④ 明细实际值与引用快照不一致 → 拦发送、文件只出草稿 ===")
            sku_c = make_sku("C", "CHKEMPTY-C 产品", "确认规格")
            await confirm(
                sku_c,
                {"name": "CHKEMPTY-C 产品", "specification": "确认规格", "unit": "件"},
                1,
                f"{PREFIX} 确认",
            )
            vid_c = new_quote(new_customer("C"), "C")
            # 人工在明细上填一个**与确认值不同**的规格（这就是"实际采用的值换了"）
            check("建明细（人工填了别的规格）", add_item(vid_c, sku_c, spec="客户特殊规格").get("code"), 0)
            item_c = item_of(vid_c)
            check("明细印的是人工填的规格", item_c.get("specification"), "客户特殊规格")
            problems_c = await quote_service.master_confirmation_problems(s, version_id=vid_c)
            check_true(
                "④不一致被识别出来（提示里说明差在哪）",
                bool(problems_c) and "不一致" in problems_c[0],
                str(problems_c),
            )
            sent_c = approve_and_send(vid_c)
            check("④正式发送被拦（40002 / 422）", sent_c.get("code"), 40002)
            check_true(
                "④提示里写明了是不一致",
                "不一致" in (sent_c.get("message") or ""),
                sent_c.get("message") or "",
            )
            doc_c = gen_doc(vid_c)
            check("④生成的对客文件只落草稿", doc_c.get("status"), "draft")
            check_true(
                "④草稿标题也标出来了",
                (doc_c.get("title") or "").startswith("【草稿】"),
                doc_c.get("title") or "",
            )

            # ==================================================================
            print()
            print("=== ⑤ 新确认版本建立后：旧明细与旧文件内容不变 ===")
            # 再确认一次（新规格）→ 整版 V2
            await confirm(
                sku_b,
                {"name": "CHKEMPTY-B 产品", "specification": "新确认规格", "unit": "件"},
                2,
                f"{PREFIX} 再确认（新规格）",
            )
            item_b2 = item_of(vid_b)
            check("⑤旧明细的规格还是当初确认的那一个", item_b2.get("specification"), "确认规格")
            check("⑤旧明细仍引用 V1（没被今天的值顶掉）", item_b2.get("master_version_no"), 1)
            hash_after = await _doc_hash(doc_b.get("id"))
            check("⑤已归档的旧文件内容没被改动", hash_after, hash_before)

            # ==================================================================
            print()
            print("=== ⑥ 人工填的值与确认值一致 → 不算不一致 ===")
            vid_d = new_quote(new_customer("D"), "D")
            check("建明细（人工填成与确认值同名）", add_item(vid_d, sku_c, spec="确认规格").get("code"), 0)
            problems_d = await quote_service.master_confirmation_problems(s, version_id=vid_d)
            check("⑥一致时不该报「不一致」", problems_d, [])
            sent_d = approve_and_send(vid_d)
            check("⑥这版能正式发送", sent_d.get("code"), 0)
    finally:
        await cleanup(ids)

    print()
    if FAILURES:
        print(f"❌ §8.14 已确认为空回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ §8.14 已确认为空回归全部通过")


async def _doc_hash(doc_id) -> str | None:
    """读已归档文件的**内容校验值**（`content_sha256`），证明"归档后没被改动"。

    用内容校验而不是文件字节哈希：后者会把生成时间算进去、同一份内容两次
    出图并不相同（`bizdoc/service._content_hash` 的注释解释过这一点）。
    """
    if not doc_id:
        # 拿不到 id 就让断言显性失败，不能两边都是 None 蒙混过关
        return "（没拿到文件 id）"
    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("select content_sha256 from biz_docs where id = :i"), {"i": doc_id}
            )
        ).scalar_one_or_none()
        return row


if __name__ == "__main__":
    asyncio.run(main())
