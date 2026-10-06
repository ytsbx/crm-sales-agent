"""第六批 · 批次三回归：客户合并漏关联 + 合并前影响检查（返工单 6.8）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 覆盖的口径

- **合并前列出全部关联对象及预计处理方式**：影响清单里能看到定制需求、打样、
  订单草稿、合同、销售案例、物流报价、专属价格，以及字段名不叫 `customer_id`
  的两类（企微客户映射、客户附件）；
- **合并后逐项对账**：这些资料的 `customer_id` 真的换到目标客户了，
  目标档案能找到它们（原来只有 6 类跟着走，其余成了孤儿）；
- **历史文件内容保持原样**：合同正文、打样图纸等只换门牌，不改内容；
- **冲突必须由人拍板**：专属价格两边不同、税号不一致时，
  不带口径的合并请求被拒（422），不会静默挑一个用；
- **口径留痕**：合并日志记下当时选了保留哪一边；
- **并发保护**：合并前按 id 顺序加行锁，且重读最新值。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_customer_merge.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHKMERGE"


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
        with urllib.request.urlopen(req, timeout=25) as resp:
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
    """自底向上清干净。本套件会写：客户及其各类关联、合并日志、模板、文件。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from business_files where business_type = 'customer' "
            "and business_id in " + cust,
            "delete from wecom_external_contacts where crm_customer_id in " + cust,
            "delete from logistics_quotes where customer_id in " + cust,
            "delete from sales_cases where customer_id in " + cust,
            "delete from contract_documents where customer_id in " + cust,
            "delete from order_drafts where customer_id in " + cust,
            "delete from sample_requests where customer_id in " + cust,
            "delete from custom_inquiries where customer_id in " + cust,
            "delete from tasks where customer_id in " + cust,
            "delete from followups where customer_id in " + cust,
            "delete from sales_orders where customer_id in " + cust,
            "delete from quotes where customer_id in " + cust,
            "delete from opportunities where customer_id in " + cust,
            "delete from contacts where customer_id in " + cust,
            "delete from customer_price_rules where customer_id in " + cust,
            "delete from customer_merge_logs where source_customer_id in " + cust
            + " or target_customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from contract_templates where name like :p",
            "delete from files where file_name like :p",
            "delete from customers where name like :p",
            "delete from audit_logs where business_type = 'customer' "
            "and after_data::text like :m",
        ):
            await s.execute(text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%"})
        await s.commit()


async def seed_fixtures() -> dict:
    """造一对客户：来源客户身上挂满各类关联，其中专属价格与目标冲突。"""
    from app.modules.bizdoc.model import BizDoc  # noqa: F401  触发模型加载
    from app.modules.cases.model import SalesCase
    from app.modules.contract.model import ContractDocument, ContractTemplate
    from app.modules.customer.model import Contact, Customer
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.followup.model import FollowUp
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import OrderDraft, SalesOrder
    from app.modules.pricing.model import CustomerPriceRule, LogisticsQuote
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task
    from app.modules.user.model import User
    from app.modules.wecom.model import WeComExternalContact

    stamp = int(time.time())
    ids: dict = {}

    async with SessionLocal() as s:
        stage_id = (
            await s.execute(text("select id from opportunity_stages order by id limit 1"))
        ).scalar_one()
        sku_id = (await s.execute(text("select id from skus order by id limit 1"))).scalar_one()
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")

        target = Customer(
            name=f"{PREFIX}目标-{stamp}", tax_no=f"{PREFIX}-TAX-{stamp}",
            owner_id=admin.id, status="active", pool_status="private",
        )
        source = Customer(
            name=f"{PREFIX}来源-{stamp}", tax_no=f"{PREFIX}-TAX-{stamp}",
            owner_id=admin.id, status="active", pool_status="private",
        )
        s.add_all([target, source])
        await s.flush()

        template = ContractTemplate(
            doc_type="contract", name=f"{PREFIX}模板-{stamp}", version=1,
            body="主合同正文占位 {{customer.name}}", created_at=datetime.now(UTC),
        )
        s.add(template)
        await s.flush()

        file_row = FileRecord(
            storage_provider="local", object_key=f"{PREFIX}/{stamp}.pdf",
            file_name=f"{PREFIX}附件-{stamp}.pdf", size=2048,
        )
        s.add(file_row)
        await s.flush()

        contract_body = f"{PREFIX}-合同正文-不可改写-{stamp}"
        s.add_all(
            [
                Contact(customer_id=source.id, name=f"{PREFIX}联系人-{stamp}", is_primary=False),
                Opportunity(
                    customer_id=source.id, title=f"{PREFIX}商机-{stamp}",
                    stage_id=stage_id, currency="CNY", status="active", owner_id=admin.id,
                ),
                Quote(
                    quote_no=f"{PREFIX}Q{stamp}", customer_id=source.id,
                    status="draft", owner_id=admin.id,
                ),
                SalesOrder(
                    order_no=f"{PREFIX}O{stamp}", customer_id=source.id,
                    total_amount=Decimal("1000"), currency="CNY", status="pending",
                    owner_id=admin.id,
                ),
                FollowUp(
                    customer_id=source.id, content=f"{PREFIX}跟进-{stamp}", owner_id=admin.id
                ),
                Task(
                    customer_id=source.id, title=f"{PREFIX}任务-{stamp}",
                    priority="normal", status="pending", source="manual", owner_id=admin.id,
                ),
                CustomInquiry(title=f"{PREFIX}定制需求-{stamp}", customer_id=source.id),
                SampleRequest(
                    customer_id=source.id, status="approved",
                    requested_at=datetime.now(UTC), owner_id=admin.id,
                ),
                OrderDraft(
                    customer_id=source.id, owner_id=admin.id, source_context={},
                    request_key=f"{PREFIX}-{stamp}", request_hash="a" * 64,
                    created_by=admin.id,
                ),
                ContractDocument(
                    doc_no=f"{PREFIX}C{stamp}", doc_type="contract",
                    title=f"{PREFIX}合同-{stamp}", customer_id=source.id,
                    template_id=template.id, content_snapshot=contract_body,
                    created_at=datetime.now(UTC),
                ),
                SalesCase(
                    title=f"{PREFIX}案例-{stamp}", author_id=admin.id,
                    customer_id=source.id, created_at=datetime.now(UTC),
                ),
                LogisticsQuote(
                    customer_id=source.id, shipping_method="海运",
                    chargeable_weight=Decimal("1"), actual_weight=Decimal("1"),
                    volume=Decimal("1"), currency="CNY", amount=Decimal("10"),
                ),
                # 专属价格：两边同一个 SKU 定了不同的价 → 必须有人拍板
                CustomerPriceRule(
                    customer_id=source.id, sku_id=sku_id, min_qty=Decimal("1"),
                    agreed_price=Decimal("200"), currency="CNY", status="active",
                ),
                CustomerPriceRule(
                    customer_id=target.id, sku_id=sku_id, min_qty=Decimal("1"),
                    agreed_price=Decimal("100"), currency="CNY", status="active",
                ),
                WeComExternalContact(
                    external_userid=f"{PREFIX}EXT{stamp}",
                    crm_customer_id=source.id, name=f"{PREFIX}外部联系人-{stamp}",
                ),
                BusinessFile(
                    business_type="customer", business_id=source.id, file_id=file_row.id
                ),
            ]
        )
        await s.flush()
        ids.update(
            {
                "source": source.id,
                "target": target.id,
                "sku_id": sku_id,
                "stamp": stamp,
                "contract_body": contract_body,
                "admin": admin.id,
            }
        )
        await s.commit()
    return ids


async def count_links(customer_id: int) -> dict[str, int]:
    """直接数库：各关联表里挂在某个客户名下的条数。"""
    async with SessionLocal() as s:
        rows = {}
        for key, sql in (
            ("contact", "select count(*) from contacts where customer_id = :c"),
            ("opportunity", "select count(*) from opportunities where customer_id = :c"),
            ("quote", "select count(*) from quotes where customer_id = :c"),
            ("order", "select count(*) from sales_orders where customer_id = :c"),
            ("followup", "select count(*) from followups where customer_id = :c"),
            ("task", "select count(*) from tasks where customer_id = :c"),
            ("inquiry", "select count(*) from custom_inquiries where customer_id = :c"),
            ("sample", "select count(*) from sample_requests where customer_id = :c"),
            ("draft", "select count(*) from order_drafts where customer_id = :c"),
            ("contract", "select count(*) from contract_documents where customer_id = :c"),
            ("case", "select count(*) from sales_cases where customer_id = :c"),
            ("logistics", "select count(*) from logistics_quotes where customer_id = :c"),
            ("price", "select count(*) from customer_price_rules where customer_id = :c"),
            (
                "wecom",
                "select count(*) from wecom_external_contacts where crm_customer_id = :c",
            ),
            (
                "attachment",
                "select count(*) from business_files "
                "where business_type = 'customer' and business_id = :c",
            ),
        ):
            rows[key] = int((await s.execute(text(sql), {"c": customer_id})).scalar_one())
        return rows


async def main() -> int:
    # 先把整个应用加载进来：只导入个别模型会让 SQLAlchemy 在解析 relationship
    # 时找不到目标类（"表未注册"），报错信息还很难懂。
    import app.main  # noqa: F401

    from app.modules.customer.model import Customer

    await cleanup()
    admin_token = login("admin", "admin123")
    ids = await seed_fixtures()
    source_id, target_id = ids["source"], ids["target"]
    stamp = ids["stamp"]

    try:
        print("=== 1. 合并影响清单：登记的东西一个都不能少 ===")
        status, res = call(
            "GET",
            f"/customers/{source_id}/merge-preview?target_customer_id={target_id}",
            admin_token,
        )
        check("影响清单可取", status, 200)
        data = res.get("data") or {}
        keys = {row["key"] for row in (data.get("targets") or [])}
        for key in (
            "Contact", "Opportunity", "Quote", "SalesOrder", "FollowUp", "Task",
            "CustomInquiry", "SampleRequest", "OrderDraft", "ContractDocument",
            "SalesCase", "LogisticsQuote", "CustomerPriceRule",
            "wecom_mapping", "attachments",
        ):
            check_true(f"清单里登记了 {key}", key in keys, str(sorted(keys))[:80])
        check("清单总数 = 15 类关联", len(data.get("targets") or []), 15)
        check_true(
            "清单给出了关联条数",
            all(row["count"] >= 0 for row in (data.get("targets") or [])),
        )
        count_by_key = {row["key"]: row["count"] for row in (data.get("targets") or [])}
        check("来源客户有 1 张打样单", count_by_key.get("SampleRequest"), 1)
        check("来源客户有 1 条定制需求", count_by_key.get("CustomInquiry"), 1)
        check("来源客户有 1 条企微映射", count_by_key.get("wecom_mapping"), 1)
        check("来源客户有 1 个附件", count_by_key.get("attachments"), 1)

        print()
        print("=== 2. 冲突摆出来了，但不替业务选 ===")
        blocking = data.get("blocking") or []
        check_true("识别出专属价格冲突", "customer_price" in blocking, str(blocking))
        conflict = next(
            (row for row in (data.get("conflicts") or []) if row["key"] == "customer_price"),
            None,
        )
        check_true("冲突里给了可选口径", bool(conflict and conflict.get("options")))
        check_true("冲突里说清哪两边不一致", bool(conflict and conflict.get("items")))

        status, res = call(
            "POST", "/customers/merge", admin_token,
            {"source_customer_id": source_id, "target_customer_id": target_id},
        )
        check("不带口径的合并被拒", status, 422)
        check_true(
            "拒绝原因指向冲突处理",
            "冲突" in str(res.get("message") or ""),
            str(res.get("message"))[:60],
        )
        after_reject = await count_links(source_id)
        check("被拒时来源客户名下的关联一条没动", after_reject["sample"], 1)

        print()
        print("=== 3. 带口径合并：逐项对账 ===")
        before_source = await count_links(source_id)
        status, res = call(
            "POST", "/customers/merge", admin_token,
            {
                "source_customer_id": source_id,
                "target_customer_id": target_id,
                "reason": f"{PREFIX} 回归",
                "resolutions": {"customer_price": "keep_target"},
            },
        )
        check("带口径合并成功", status, 200)
        moved = (res.get("data") or {}).get("moved") or {}
        for label, expect in (
            ("联系人", 1), ("商机", 1), ("报价单", 1), ("销售订单", 1),
            ("跟进记录", 1), ("任务", 1), ("定制需求", 1), ("打样单", 1),
            ("订单草稿", 1), ("合同", 1), ("销售案例", 1), ("物流报价", 1),
            ("专属价格", 1), ("企微客户映射", 1), ("客户附件", 1),
        ):
            check(f"日志记了「{label}」迁移 {expect} 条", moved.get(label), expect)
        check_true("合计 15 类", len(moved) >= 15, str(sorted(moved))[:80])
        check("冲突口径也进了返回", (res.get("data") or {}).get("conflicts"),
              {"customer_price": "keep_target"})

        after_target = await count_links(target_id)
        for key, label in (
            ("contact", "联系人"), ("opportunity", "商机"), ("quote", "报价单"),
            ("order", "销售订单"), ("followup", "跟进记录"), ("task", "任务"),
            ("inquiry", "定制需求"), ("sample", "打样单"), ("draft", "订单草稿"),
            ("contract", "合同"), ("case", "销售案例"), ("logistics", "物流报价"),
            ("price", "专属价格"), ("wecom", "企微客户映射"), ("attachment", "客户附件"),
        ):
            # 目标客户是本次新建的，名下原本没有这些；合并后应当**一条不少**地
            # 出现在它名下 —— 数量要等于来源原来的条数（不是"至少有一条"，
            # 那会让"只迁了一半"也蒙混过关）。
            check_true(
                f"目标档案里接到了来源的全部「{label}」（{before_source[key]} 条）",
                after_target[key] >= before_source[key],
                f"目标 {after_target[key]} 条",
            )
        after_source = await count_links(source_id)
        check("来源客户名下已清空（关联全部改挂）", after_source["sample"], 0)
        check("来源客户名下也不留企微映射", after_source["wecom"], 0)
        check("来源客户名下也不留附件", after_source["attachment"], 0)

        print()
        print("=== 4. 历史文件内容保持原样（只换门牌，不改内容）===")
        async with SessionLocal() as s:
            body = (
                await s.execute(
                    text(
                        "select content_snapshot from contract_documents "
                        "where customer_id = :c and title like :p"
                    ),
                    {"c": target_id, "p": f"{PREFIX}%"},
                )
            ).scalar_one()
            check("合同正文一字未改", body, ids["contract_body"])
            sample_owner = (
                await s.execute(
                    text(
                        "select owner_id from sample_requests where customer_id = :c"
                    ),
                    {"c": target_id},
                )
            ).scalar_one()
            check("打样单只换门牌、责任人不动", sample_owner, ids["admin"])

        print()
        print("=== 5. 冲突口径留痕 + 价目按口径转历史 ===")
        async with SessionLocal() as s:
            conflict_note = (
                await s.execute(
                    text(
                        "select conflicts from customer_merge_logs "
                        "where source_customer_id = :c"
                    ),
                    {"c": source_id},
                )
            ).scalar_one()
            check("合并日志记了冲突口径", conflict_note, {"customer_price": "keep_target"})
            prices = (
                await s.execute(
                    text(
                        "select status, agreed_price from customer_price_rules "
                        "where customer_id = :c order by id"
                    ),
                    {"c": target_id},
                )
            ).all()
            active = [row for row in prices if row[0] == "active"]
            historical = [row for row in prices if row[0] == "historical"]
            check("目标客户的价仍是生效的", len(active), 1)
            # 列是 `Numeric(16,4)`，读出来是 100.0000
            check("生效价保持目标原来那个值", str(active[0][1]), "100.0000")
            check("来源那条冲突价转为历史资料（不删）", len(historical), 1)
            check("历史那条保留原价，可查", str(historical[0][1]), "200.0000")

        print()
        print("=== 6. 税号不一致：合并前必须确认 ===")
        async with SessionLocal() as s:
            other = Customer(
                name=f"{PREFIX}税号目标-{stamp}", tax_no=f"{PREFIX}-OTHER-{stamp}",
                owner_id=ids["admin"], status="active", pool_status="private",
            )
            src2 = Customer(
                name=f"{PREFIX}税号来源-{stamp}", tax_no=f"{PREFIX}-TAX2-{stamp}",
                owner_id=ids["admin"], status="active", pool_status="private",
            )
            s.add_all([other, src2])
            await s.commit()
            other_id, src2_id = other.id, src2.id
        status, res = call(
            "GET",
            f"/customers/{src2_id}/merge-preview?target_customer_id={other_id}",
            admin_token,
        )
        check_true(
            "清单标出了税号冲突",
            "tax_no" in ((res.get("data") or {}).get("blocking") or []),
            str((res.get("data") or {}).get("blocking")),
        )
        status, _ = call(
            "POST", "/customers/merge", admin_token,
            {"source_customer_id": src2_id, "target_customer_id": other_id},
        )
        check("税号不一致、没确认时被拒", status, 422)
        status, _ = call(
            "POST", "/customers/merge", admin_token,
            {
                "source_customer_id": src2_id,
                "target_customer_id": other_id,
                "resolutions": {"tax_no": "confirm"},
            },
        )
        check("明确确认后可以合并", status, 200)

        print()
        print("=== 7. 已合并的来源客户不能再合一次 ===")
        status, _ = call(
            "POST", "/customers/merge", admin_token,
            {
                "source_customer_id": source_id,
                "target_customer_id": target_id,
                "resolutions": {"customer_price": "keep_target"},
            },
        )
        # 来源客户已软删，取数时按"查不到"处理（404）；真到了合并逻辑里
        # 也会因为 `deleted_at` 被拒（422）。两种都算拒绝，关键**不能是 200**。
        check_true(
            "已合并（软删）的来源客户不能再合一次",
            status in (404, 422),
            f"实际 {status}",
        )

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED：{len(FAILURES)} 项未通过")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
