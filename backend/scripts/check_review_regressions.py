"""Focused API regressions for fixes from the document-led code review.

This suite only talks to CRM_TEST_BASE_URL (default loopback API). Run it against a
disposable database with DINGTALK_PUSH_OFF=1, WECOM_PUSH_OFF=1 and
SCHEDULER_ENABLED=false. It does not call email, DingTalk, WeCom, ERP or AI services.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Barrier
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import text
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal

BASE = require_api_base()
PREFIX = f"CHKREV{int(time.time())}"
FAILURES: list[str] = []
FIXTURE_IDS: dict[str, int] = {}


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, *, token: str | None = None, body=None, headers=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def login(username: str, password: str) -> str:
    status, result = call("POST", "/auth/login", body={"username": username, "password": password})
    if status != 200:
        raise RuntimeError(f"login {username} failed: HTTP {status} {result}")
    return result["data"]["access_token"]


async def create_fixtures() -> None:
    import app.main  # noqa: F401
    _ = app.main

    from app.modules.contract.model import ContractTemplate
    from app.modules.customer.model import Customer
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.opportunity.model import Opportunity, OpportunityItem
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import PaymentRecord
    from app.modules.pricing.model import ProductCost
    from app.modules.product.model import Product, Sku
    from app.modules.quote.model import Quote, QuoteVersion

    now = datetime.now(UTC)
    async with SessionLocal() as session:
        owner = (await session.execute(
            text("select id from users where username='zhangsan'")
        )).scalar_one()
        other = (await session.execute(
            text("select id from users where username='lisi'")
        )).scalar_one()
        admin = (await session.execute(
            text("select id from users where username='admin'")
        )).scalar_one()

        own_customer = Customer(
            name=f"{PREFIX}-own", customer_type="企业", country="中国", level="B",
            status="active", pool_status="private", owner_id=owner, created_by=owner,
        )
        other_customer = Customer(
            name=f"{PREFIX}-other", customer_type="企业", country="中国", level="B",
            status="active", pool_status="private", owner_id=other, created_by=other,
        )
        session.add_all([own_customer, other_customer])
        await session.flush()

        template = ContractTemplate(
            doc_type="contract", name=f"{PREFIX}-template", version=1,
            body="合同 {{customer.name}}", enabled=True, created_by=admin, created_at=now,
        )
        foreign_order = SalesOrder(
            order_no=f"{PREFIX}-ORDER", customer_id=other_customer.id, owner_id=other,
            sales_owner_id=other, total_amount=Decimal("10"), currency="CNY",
            status="pending", created_by=other,
        )
        foreign_quote = Quote(
            quote_no=f"{PREFIX}-QUOTE", customer_id=other_customer.id, owner_id=other,
            status="draft", created_by=other,
        )
        session.add_all([template, foreign_order, foreign_quote])
        await session.flush()

        own_order = SalesOrder(
            order_no=f"{PREFIX}-OWNORDER", customer_id=own_customer.id, owner_id=owner,
            sales_owner_id=owner, total_amount=Decimal("100"), currency="CNY",
            status="pending", created_by=owner,
        )
        rejected_payment = PaymentRecord(
            order_id=own_order.id if own_order.id else 0,
            # 统一用 UTC 日期（与业务判据同一个钟），别用本地 date.today()
            received_date=datetime.now(UTC).date(), received_amount=Decimal("20"),
            currency="CNY", status="rejected", created_by=admin, created_at=now,
        )
        pending_payment = PaymentRecord(
            order_id=own_order.id if own_order.id else 0,
            received_date=datetime.now(UTC).date(), received_amount=Decimal("30"),
            currency="CNY", status="pending", created_by=admin, created_at=now,
        )
        # Flush the order before creating a record that references it.
        session.add(own_order)
        await session.flush()
        rejected_payment.order_id = own_order.id
        pending_payment.order_id = own_order.id
        session.add_all([rejected_payment, pending_payment])

        file_record = FileRecord(
            storage_provider="local", object_key=f"{PREFIX}/foreign.pdf",
            file_name=f"{PREFIX}-foreign.pdf", mime_type="application/pdf", size=1,
            uploaded_by=other, created_at=now,
        )
        product = Product(name=f"{PREFIX}-product", status="active", created_by=admin)
        session.add_all([file_record, product])
        await session.flush()
        foreign_attachment = BusinessFile(
            business_type="customer", business_id=other_customer.id, file_id=file_record.id,
        )

        sku = Sku(product_id=product.id, sku_code=f"{PREFIX}-SKU", name="无售价测试品", status="active")
        session.add(sku)
        await session.flush()
        cost = ProductCost(
            sku_id=sku.id, purchase_cost=Decimal("10"), production_cost=Decimal("0"),
            package_cost=Decimal("0"), processing_cost=Decimal("0"), currency="CNY",
            effective_from=datetime.now(UTC).date() - timedelta(days=1), created_by=admin,
        )
        stage_id = (await session.execute(
            text("select id from opportunity_stages where status='active' order by sequence limit 1")
        )).scalar_one()
        opportunity = Opportunity(
            customer_id=own_customer.id, title=f"{PREFIX}-opportunity", stage_id=stage_id,
            expected_amount=Decimal("88"), currency="CNY", owner_id=owner,
            status="open", created_by=owner,
        )
        session.add_all([cost, opportunity, foreign_attachment])
        await session.flush()
        opp_item = OpportunityItem(
            opportunity_id=opportunity.id, sku_id=sku.id, quantity=Decimal("2"),
            target_price=Decimal("88"), currency="CNY",
        )
        session.add(opp_item)

        expired_quote = Quote(
            quote_no=f"{PREFIX}-EXPIRED", customer_id=own_customer.id, owner_id=owner,
            status="sent",
            # 用 **UTC** 日期：判过期的是 `quote_is_expired`（内部取 UTC 今天）。
            # 原来用本地 `date.today()`，本地比 UTC 早 8 小时，UTC 16:00 后本地已跨天，
            # 夹具的"昨天"正好等于 UTC 今天 → 不再算过期 → 这条断言每天后半天必挂。
            valid_until=datetime.now(UTC).date() - timedelta(days=1), created_by=owner,
        )
        session.add(expired_quote)
        await session.flush()
        expired_version = QuoteVersion(
            quote_id=expired_quote.id, version_no=1, currency="CNY",
            approval_status="approved", created_by=owner, created_at=now, sent_at=now,
        )
        session.add(expired_version)
        await session.flush()
        expired_quote.current_version_id = expired_version.id

        await session.commit()
        FIXTURE_IDS.update({
            "own_customer": own_customer.id, "other_customer": other_customer.id,
            "template": template.id, "foreign_order": foreign_order.id,
            "foreign_quote": foreign_quote.id, "own_order": own_order.id,
            "payment": rejected_payment.id, "pending_payment": pending_payment.id,
            "file": file_record.id, "sku": sku.id,
            "product": product.id, "opportunity": opportunity.id,
            "expired_quote": expired_quote.id, "expired_version": expired_version.id,
        })


async def cleanup() -> None:
    """Remove suite-created rows so the CI fixture guard remains meaningful."""
    if not FIXTURE_IDS:
        return
    import app.main  # noqa: F401
    _ = app.main
    cids = (FIXTURE_IDS.get("own_customer", -1), FIXTURE_IDS.get("other_customer", -1))
    async with SessionLocal() as session:
        for statement, params in (
            ("delete from audit_logs where (business_type='quote' and business_id in (select id from quotes where quote_no like :p or id=:generated)) or (business_type='contract' and business_id in (select id from contract_documents where customer_id in (:own, :other)))", {"p": f"{PREFIX}%", "generated": FIXTURE_IDS.get("generated_quote", -1), "own": cids[0], "other": cids[1]}),
            ("delete from business_files where business_type='customer' and business_id in (:own, :other)",
             {"own": cids[0], "other": cids[1]}),
            ("delete from business_files where business_type='contract' and business_id in (select id from contract_documents where customer_id in (:own, :other))", {"own": cids[0], "other": cids[1]}),
            ("delete from contract_documents where customer_id in (:own, :other)", {"own": cids[0], "other": cids[1]}),
            ("delete from contract_templates where name like :p", {"p": f"{PREFIX}%"}),
            ("delete from notifications where business_type='order' and business_id in (select id from sales_orders where order_no like :p)", {"p": f"{PREFIX}%"}),
            ("delete from business_events where business_type='order' and business_id in (select id from sales_orders where order_no like :p)", {"p": f"{PREFIX}%"}),
            ("delete from audit_logs where business_type='payment' and business_id in (select id from payment_records where order_id in (select id from sales_orders where order_no like :p))", {"p": f"{PREFIX}%"}),
            ("delete from payment_records where order_id in (select id from sales_orders where order_no like :p)", {"p": f"{PREFIX}%"}),
            ("delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where quote_no like :p or id=:generated))", {"p": f"{PREFIX}%", "generated": FIXTURE_IDS.get("generated_quote", -1)}),
            ("delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where quote_no like :p or id=:generated))", {"p": f"{PREFIX}%", "generated": FIXTURE_IDS.get("generated_quote", -1)}),
            ("delete from quote_versions where quote_id in (select id from quotes where quote_no like :p or id=:generated)", {"p": f"{PREFIX}%", "generated": FIXTURE_IDS.get("generated_quote", -1)}),
            ("delete from quotes where quote_no like :p or id=:generated", {"p": f"{PREFIX}%", "generated": FIXTURE_IDS.get("generated_quote", -1)}),
            ("delete from opportunity_items where opportunity_id in (select id from opportunities where title like :p)", {"p": f"{PREFIX}%"}),
            ("delete from opportunity_stage_history where opportunity_id in (select id from opportunities where title like :p)", {"p": f"{PREFIX}%"}),
            ("delete from opportunities where title like :p", {"p": f"{PREFIX}%"}),
            ("delete from sales_order_items where order_id in (select id from sales_orders where order_no like :p)", {"p": f"{PREFIX}%"}),
            ("delete from order_status_history where order_id in (select id from sales_orders where order_no like :p)", {"p": f"{PREFIX}%"}),
            ("delete from sales_orders where order_no like :p", {"p": f"{PREFIX}%"}),
            ("delete from product_costs where sku_id in (select id from skus where sku_code like :p)", {"p": f"{PREFIX}%"}),
            ("delete from price_rules where sku_id in (select id from skus where sku_code like :p)", {"p": f"{PREFIX}%"}),
            ("delete from customer_price_rules where customer_id in (:own, :other)",
             {"own": cids[0], "other": cids[1]}),
            ("delete from skus where sku_code like :p", {"p": f"{PREFIX}%"}),
            ("delete from products where name like :p", {"p": f"{PREFIX}%"}),
            ("delete from files where file_name like :p", {"p": f"{PREFIX}%"}),
            ("delete from customers where name like :p", {"p": f"{PREFIX}%"}),
        ):
            await session.execute(text(statement), params)
        await session.commit()


def main() -> None:
    import app.main  # noqa: F401
    _ = app.main

    api_host = urlparse(BASE).hostname
    db_url = urlparse(settings.database_url)
    db_name = db_url.path.lstrip("/").lower()
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    isolated_db = "test" in db_name or os.getenv("CI", "").lower() == "true"
    if api_host not in loopback_hosts or db_url.hostname not in loopback_hosts or not isolated_db:
        raise SystemExit(
            "拒绝运行：回归脚本只允许访问本机 API 和一次性测试数据库 "
            "（本机数据库名须含 test；CI 使用独立 PostgreSQL service）"
        )
    if not settings.dingtalk_push_off or not settings.wecom_push_off or settings.scheduler_enabled:
        raise SystemExit(
            "拒绝运行：请先设置 DINGTALK_PUSH_OFF=1、WECOM_PUSH_OFF=1、"
            "SCHEDULER_ENABLED=false 并重启本机后端"
        )

    async def run():
        await create_fixtures()
        try:
            zhangsan = login("zhangsan", "123456")
            admin = login("admin", "admin123")
            own_customer = FIXTURE_IDS["own_customer"]

            print("=== Contract cross-customer links and signature file scope ===")
            status, result = call("POST", "/contract-documents", token=zhangsan, body={
                "template_id": FIXTURE_IDS["template"], "customer_id": own_customer,
                "order_id": FIXTURE_IDS["foreign_order"],
            })
            check("cross-customer order cannot be linked", status == 422, f"HTTP {status} {result}")
            status, result = call("POST", "/contract-documents", token=zhangsan, body={
                "template_id": FIXTURE_IDS["template"], "customer_id": own_customer,
                "quote_id": FIXTURE_IDS["foreign_quote"],
            })
            check("cross-customer quote cannot be linked", status == 422, f"HTTP {status} {result}")
            status, result = call("POST", "/contract-documents", token=zhangsan, body={
                "template_id": FIXTURE_IDS["template"], "customer_id": own_customer,
            })
            doc_id = result.get("data", {}).get("id") if status == 200 else None
            check("valid own-customer contract draft created", status == 200, f"HTTP {status} {result}")
            if doc_id:
                status, result = call("POST", f"/contract-documents/{doc_id}/sign", token=zhangsan,
                                     body={"file_id": FIXTURE_IDS["file"]})
                check("foreign customer's file cannot sign own contract", status == 403,
                      f"HTTP {status} {result}")

            print("=== Payment terminal-state guard ===")
            status, result = call("POST", f"/payments/{FIXTURE_IDS['payment']}/confirm", token=admin,
                                  body={"comment": "regression"})
            check("rejected payment cannot be confirmed", status == 400, f"HTTP {status} {result}")
            status, result = call("POST", f"/payments/{FIXTURE_IDS['payment']}/reject", token=admin,
                                  body={"comment": "regression"})
            check("rejected payment cannot be rejected twice", status == 400, f"HTTP {status} {result}")

            print("=== Concurrent payment confirm/reject ===")
            barrier = Barrier(2)

            def concurrent_action(action: str):
                barrier.wait(timeout=10)
                return call("POST", f"/payments/{FIXTURE_IDS['pending_payment']}/{action}",
                            token=admin, body={"comment": "concurrency regression"})

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(concurrent_action, "confirm"),
                           pool.submit(concurrent_action, "reject")]
                race_results = [future.result(timeout=30) for future in futures]
            race_statuses = sorted(status for status, _ in race_results)
            check("only one concurrent payment transition succeeds", race_statuses == [200, 400],
                  f"HTTP statuses={race_statuses}; results={race_results}")
            status, result = call("GET", f"/payments/{FIXTURE_IDS['pending_payment']}", token=admin)
            final_state = result.get("data", {}).get("status") if status == 200 else None
            check("concurrent payment ends in one terminal state", status == 200
                  and final_state in {"confirmed", "rejected"},
                  f"HTTP {status}; final status={final_state}")

            print("=== Concurrent contract void (row lock) ===")
            # 作废/签署都不该"点两次都成功"：没有行锁时两个请求都读到 draft、
            # 都通过检查，台账上会留下两次作废记录（各自一个理由）。
            if doc_id:
                void_barrier = Barrier(2)

                def concurrent_void(tag: str):
                    void_barrier.wait(timeout=10)
                    return call("POST", f"/contract-documents/{doc_id}/void",
                                token=zhangsan, body={"reason": f"race-{tag}"})

                with ThreadPoolExecutor(max_workers=2) as pool:
                    void_futures = [pool.submit(concurrent_void, "a"),
                                    pool.submit(concurrent_void, "b")]
                    void_results = [future.result(timeout=30) for future in void_futures]
                void_statuses = sorted(status for status, _ in void_results)
                # 后到的那个走到 void_document 里"已经是作废状态"的分支，
                # 那条 AppError 没指定 http_status，按默认的 400 出来
                check("only one concurrent contract void succeeds",
                      void_statuses == [200, 400],
                      f"HTTP statuses={void_statuses}; results={void_results}")

            print("=== Concurrent contract template version ===")
            # 同名模板同时新建：没有唯一约束 + 重取逻辑时，两边都算出 v1、
            # 各插一行 —— 历史文件钉死的"模板 v1"就有两份不同正文。
            template_name = f"{PREFIX}-TPL-RACE"
            tpl_barrier = Barrier(2)

            def concurrent_template(tag: str):
                tpl_barrier.wait(timeout=10)
                return call("POST", "/contract-templates", token=admin, body={
                    "doc_type": "contract", "name": template_name,
                    "body": f"甲方：{{{{party}}}}  # {tag}",
                })

            with ThreadPoolExecutor(max_workers=2) as pool:
                tpl_futures = [pool.submit(concurrent_template, "a"),
                               pool.submit(concurrent_template, "b")]
                tpl_results = [future.result(timeout=30) for future in tpl_futures]
            tpl_statuses = sorted(status for status, _ in tpl_results)
            tpl_versions = sorted(
                result.get("data", {}).get("version")
                for _, result in tpl_results
                if isinstance(result.get("data"), dict) and "version" in result["data"]
            )
            check("concurrent same-name templates get distinct versions",
                  tpl_statuses == [200, 200] and tpl_versions == [1, 2],
                  f"HTTP statuses={tpl_statuses}; versions={tpl_versions}; results={tpl_results}")

            status, result = call("POST", "/contract-templates", token=admin, body={
                "doc_type": "contract", "name": template_name, "body": "甲方：{{party}}  # c",
            })
            check("a third same-name template continues the version chain",
                  status == 200 and result.get("data", {}).get("version") == 3,
                  f"HTTP {status} {result}")

            if settings.erp_webhook_secret:
                print("=== ERP callback uses normal order lifecycle guards ===")
                callback_headers = {"X-ERP-Secret": settings.erp_webhook_secret}
                status, result = call(
                    "POST", "/webhooks/erp/order-status", body={
                        "order_no": f"{PREFIX}-OWNORDER", "status": "Cancelled",
                    }, headers=callback_headers,
                )
                check("ERP cancellation callback is accepted", status == 200
                      and result.get("data", {}).get("changed") is True
                      and result.get("data", {}).get("status") == "cancelled",
                      f"HTTP {status} {result}")
                async with SessionLocal() as session:
                    cancelled_at = (await session.execute(text(
                        "select cancelled_at from sales_orders where id=:id"
                    ), {"id": FIXTURE_IDS["own_order"]})).scalar_one()
                    history_count = (await session.execute(text(
                        "select count(*) from order_status_history "
                        "where order_id=:id and source='ERP' and new_status='cancelled'"
                    ), {"id": FIXTURE_IDS["own_order"]})).scalar_one()
                check("ERP cancellation records cancelled_at and history",
                      cancelled_at is not None and history_count == 1,
                      f"cancelled_at={cancelled_at}; ERP history={history_count}")
                status, result = call(
                    "POST", "/webhooks/erp/order-status", body={
                        "order_no": f"{PREFIX}-OWNORDER", "status": "Finished",
                    }, headers=callback_headers,
                )
                check("cancelled order cannot be reopened by ERP callback", status == 200
                      and result.get("data", {}).get("changed") is False
                      and result.get("data", {}).get("status") == "cancelled",
                      f"HTTP {status} {result}")

            print("=== ERP manual refresh also goes through order lifecycle guards (§4.1.8) ===")
            # 手动刷新（`POST /orders/{id}/refresh-status`）原来**直接赋值** order.status：
            # ERP 能把已取消的订单"复活"成已发货，也能在还有未发量时把整单标成完成——
            # §3.5 刚立的闸门被这条后门绕过去（webhook 那条早就改了，这条当时漏了）。
            # 这里用**桩适配器**在被测进程内替换，不碰任何外部系统。
            from app.modules.erp import service as erp_service
            from app.modules.order.model import SalesOrder

            class _StubAdapter:
                label = "桩ERP"
                STATUS_MAP = {"Finished": "completed"}

                async def fetch_order_status(self, erp_order_id):
                    return {"status": "completed", "raw_status": "Finished",
                            "raw": {"stub": True}}

            async with SessionLocal() as session:
                # 夹具准备：把这单置成「已取消」并挂上外部单号（直接改库=布置现场，
                # 不是被测行为）
                await session.execute(text(
                    "update sales_orders set status='cancelled', erp_order_id='STUB-REF-1' "
                    "where id=:id"
                ), {"id": FIXTURE_IDS["own_order"]})
                await session.commit()
                order = await session.get(SalesOrder, FIXTURE_IDS["own_order"])
                original_adapter = erp_service.get_adapter
                erp_service.get_adapter = lambda: _StubAdapter()
                try:
                    refreshed = await erp_service.refresh_status(
                        session, order=order, operator_id=None
                    )
                    await session.commit()
                finally:
                    erp_service.get_adapter = original_adapter
                await session.refresh(order)
                check("manual ERP refresh cannot resurrect a cancelled order",
                      refreshed["changed"] is False
                      and refreshed["status"] == "cancelled"
                      and order.status == "cancelled",
                      f"result={refreshed}; db_status={order.status}")
                check("the block reason is reported instead of silently doing nothing",
                      bool(refreshed.get("blocked_reason")),
                      f"blocked_reason={refreshed.get('blocked_reason')}")


            status, result = call("POST", f"/quote-versions/{FIXTURE_IDS['expired_version']}/accept",
                                  token=zhangsan)
            check("expired quote cannot be accepted", status == 422, f"HTTP {status} {result}")

            status, result = call("POST", "/quotes", token=zhangsan,
                                  body={"opportunity_id": FIXTURE_IDS["opportunity"], "currency": "CNY"})
            quote_data = result.get("data", {})
            check("opportunity quote without guide price is created with warning", status == 200
                  and bool(quote_data.get("warnings")), f"HTTP {status} {result}")
            if status == 200:
                FIXTURE_IDS["generated_quote"] = quote_data["quote_id"]
                version_id = quote_data["version_id"]
                status2, result2 = call("GET", f"/quote-versions/{version_id}/items", token=zhangsan)
                items = result2.get("data", []) if status2 == 200 else None
                check("customer target price was not copied into quote line", status2 == 200 and items == [],
                      f"HTTP {status2} {result2}")
                status3, result3 = call("POST", f"/quote-versions/{version_id}/items/batch",
                                        token=zhangsan, body=[{
                                            "sku_id": FIXTURE_IDS["sku"], "quantity": 2,
                                        }])
                check("manual SKU quote cannot fall back to cost estimate", status3 == 422,
                      f"HTTP {status3} {result3}")
                status4, result4 = call("POST", f"/quote-versions/{version_id}/items/batch",
                                        token=zhangsan, body=[{
                                            "sku_id": FIXTURE_IDS["sku"], "quantity": 2,
                                            "quoted_price": "55.00",
                                        }])
                check("explicit positive manual price is accepted", status4 == 200,
                      f"HTTP {status4} {result4}")
                status5, result5 = call("GET", f"/quote-versions/{version_id}/items", token=zhangsan)
                manual_items = result5.get("data", []) if status5 == 200 else []
                check("manual price is stored as quoted", status5 == 200 and len(manual_items) == 1
                      and manual_items[0].get("quoted_price") == 55.0,
                      f"HTTP {status5} {result5}")
        finally:
            await cleanup()

    asyncio.run(run())
    if FAILURES:
        raise SystemExit(f"FAIL: {', '.join(FAILURES)}")
    print("All review regressions passed.")


if __name__ == "__main__":
    main()
