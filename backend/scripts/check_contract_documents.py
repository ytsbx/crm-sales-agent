"""合同与月结协议：阶段 A 修复的回归（2026-10-05，外部审查第二批）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

覆盖不需要上传文件的 8 处（阶段 A 的 4 处 + 阶段 B 的 3 处 + 详情页筛选 1 处）：

1. **模板缺项不再被静默清空**：填不上的占位符（模板写错 / 对象没这个字段 /
   这份资料是空的 / 本次没填）连 `{{ }}` 一起原样留在正文里，并作为
   `missing_fields` 报出来。修复前是一律换成空串 —— 正文上一片空白，
   看不出是模板写错了、还是这个客户确实没填，两种情况的处理方式完全不同。
2. **生成请求防重**：同一个 `request_key` 重复提交只落一份文档。
   修复前重试/连点两次会在台账上留下两份内容相同、编号不同的草稿。
3. **台账分页**：`page / page_size / total` 真的生效。修复前是 `.limit(500)`
   硬顶，第 501 份合同永远不出现、也没有任何提示。
4. **作废原因不许是纯空白**：原来的 `min_length=1` 挡不住 `"   "`。
5. **报价金额取「所选版本」**：`{{quote.total_amount}}` 修复前取的是报价单本体，
   而 `quotes` 表上根本没有金额字段 → 这条恒为缺项，合同上的金额一直是空的。
   现在先查选定版本再回落本体；台账还要能回答"依据的是 V1 还是 V2"。
6. **抬头进快照 + 生成稿落盘**：生成时把公司名/客户名/单号存进 `_header`，
   并把 PDF 渲染一次落盘（含 sha256）。此后客户改名不影响原件，
   同一编号两次下载逐字节一致。修复前是每次下载拿当前资料重新渲染。
7. **登记签署必须有正式依据**：「提前备合同」口径——条款可以先备，
   但签署前得挂上正式订单或**已发送/已接受**的报价（月结协议不受此限）。
8. **详情页按订单/报价筛合同**：`order_id` / `quote_id` 走服务端筛选，
   而且**不能绕过数据范围**——新增筛选参数最容易出的漏洞就是
   "筛选条件把范围条件覆盖掉了"，拿别人的 order_id 就查到了别人的合同。

已签合同作废要主管、签署件类型统一这些要用上传夹具的断言，
放在 `check_attachment_sample_guards.py` 里（那边已经有签好的合同）。

跑法（隔离库；不要对着默认开发库跑，`check_*` 会清库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \
      PYTHONPATH=. .venv/bin/python scripts/check_contract_documents.py
"""

import asyncio
import json
import os
import time
from urllib.parse import urlparse

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKCON{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def rejected(status: int) -> bool:
    """入参非法：可能回 400（参数错误）或 422（业务规则拒绝），都算拒绝。"""
    return status in (400, 422)


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

    admin = login("admin", "admin123")
    zhangsan = login("zhangsan", "123456")
    customer_ids: list[int] = []
    doc_ids: list[int] = []
    template_ids: list[int] = []
    quote_ids: list[int] = []
    order_ids: list[int] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        async with SessionLocal() as session:
            zhangsan_id = (
                await session.execute(select(User.id).where(User.username == "zhangsan"))
            ).scalar_one()
            own = Customer(name=f"{MARKER}客户", owner_id=zhangsan_id)
            session.add(own)
            await session.flush()
            customer_ids.append(own.id)
            await session.commit()

        print("=== 1. 模板缺项不再被静默清空 ===")
        template = api(
            "POST", "/contract-templates", token=admin,
            body={
                "doc_type": "contract",
                "name": MARKER,
                "body": (
                    "客户：{{customer.name}}\n"
                    "电话：{{customer.nane}}\n"      # 模板把字段名写错了
                    "来源：{{nosuch.field}}\n"       # 连来源都不存在
                    "付款：{{extra.付款方式}}\n"      # 本次没填
                    "日期：{{today}}\n"
                ),
            },
        )
        template_ids.append(template["id"])

        status, envelope = call(
            "POST", "/contract-documents", token=zhangsan,
            body={"template_id": template["id"], "customer_id": own.id},
        )
        assert status == 200, envelope
        doc = envelope["data"]
        doc_ids.append(doc["id"])
        body = doc["content_snapshot"]
        missing = doc["missing_fields"]

        check("写错的字段名原样留在正文里（不再静默变空白）",
              "{{customer.nane}}" in body, body.replace("\n", " / ")[:70])
        check("不存在的来源也原样保留", "{{nosuch.field}}" in body)
        check("本次没填的 extra 项也原样保留", "{{extra.付款方式}}" in body)
        check("填得上的字段照常填上", f"客户：{own.name}" in body, body.split("\n")[0])
        check("{{today}} 照常替换成日期", "{{today}}" not in body)
        check("缺项清单把三项都报了出来", len(missing) == 3, missing)
        check("写错的字段名归为「对象上没有这个字段」",
              "没有这个字段" in (missing.get("customer.nane") or ""), missing.get("customer.nane"))
        check("不存在的来源归为「来源不存在」",
              "来源不存在" in (missing.get("nosuch.field") or ""), missing.get("nosuch.field"))
        check("没填的人工项归为「本次没有填写」",
              "本次没有填写" in (missing.get("extra.付款方式") or ""), missing.get("extra.付款方式"))
        check("接口提示里也说明了有几处没填上",
              "没填上" in envelope["message"], envelope["message"])

        print("=== 2. 生成请求防重（网络重试不该多出一份） ===")
        key = f"{MARKER}-KEY"
        first = api("POST", "/contract-documents", token=zhangsan,
                    body={"template_id": template["id"], "customer_id": own.id, "request_key": key})
        doc_ids.append(first["id"])
        second = api("POST", "/contract-documents", token=zhangsan,
                     body={"template_id": template["id"], "customer_id": own.id, "request_key": key})
        check("同一个 request_key 重复提交返回的是同一份",
              first["id"] == second["id"], f"{first['doc_no']} vs {second['doc_no']}")
        check("没有因此多出一份文档",
              len(api("GET", f"/contract-documents?customer_id={own.id}&page_size=100", token=zhangsan)["items"]) == 2,
              "客户名下应只有：缺项那份 + 防重那份")
        third = api("POST", "/contract-documents", token=zhangsan,
                    body={"template_id": template["id"], "customer_id": own.id,
                          "request_key": f"{key}-2"})
        doc_ids.append(third["id"])
        check("换一个 request_key 才是新的一份", third["id"] != first["id"])

        print("=== 3. 台账分页 ===")
        page1 = api("GET", f"/contract-documents?customer_id={own.id}&page=1&page_size=2",
                    token=zhangsan)
        check("返回分页结构（items/page/page_size/total）",
              all(k in page1 for k in ("items", "page", "page_size", "total")), list(page1))
        check("page_size 真的生效", len(page1["items"]) == 2, len(page1["items"]))
        check("total 是总数、不是当页条数", page1["total"] == 3, page1["total"])
        page2 = api("GET", f"/contract-documents?customer_id={own.id}&page=2&page_size=2",
                    token=zhangsan)
        check("第二页拿到的是剩下的那条", len(page2["items"]) == 1, len(page2["items"]))
        check("两页不重复", page1["items"][0]["id"] != page2["items"][0]["id"])

        print("=== 4. 作废原因不许是纯空白 ===")
        status, result = call("POST", f"/contract-documents/{doc['id']}/void",
                              token=zhangsan, body={"reason": "   "})
        check("纯空白的作废原因被拒", rejected(status), f"HTTP {status} {result}")
        status, result = call("POST", f"/contract-documents/{doc['id']}/void",
                              token=zhangsan, body={"reason": ""})
        check("空字符串的作废原因也被拒", rejected(status), f"HTTP {status}")
        status, result = call("POST", f"/contract-documents/{doc['id']}/void",
                              token=zhangsan, body={"reason": f"{MARKER} 客户取消订单"})
        check("写了真实原因就正常作废", status == 200, f"HTTP {status} {result}")
        after = api("GET", f"/contract-documents/{doc['id']}", token=zhangsan)
        check("台账上是作废状态", after["status"] == "void", after["status"])
        check("真实原因确实存下来了（不是写死的「页面作废」）",
              after["void_reason"] == f"{MARKER} 客户取消订单", after["void_reason"])

        print("=== 5. 报价金额取「所选版本」，抬头进快照 ===")
        import hashlib
        import urllib.request
        from datetime import UTC, datetime
        from decimal import Decimal

        from app.modules.quote.model import Quote, QuoteVersion

        async with SessionLocal() as session:
            quote = Quote(
                quote_no=f"{MARKER}Q1",
                customer_id=own.id,
                owner_id=zhangsan_id,
                status="sent",
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add(quote)
            await session.flush()
            version = QuoteVersion(
                quote_id=quote.id,
                version_no=2,
                total_amount=Decimal("12345.67"),
                currency="CNY",
                payment_terms="月结30天",
                approval_status="approved",
                # 签署判据看的是「**这一版**客户见过没有」（版本自己的发送时间），
                # 不是报价的状态 —— 只改 quote.status 造出来的"已发送"，
                # 版本这边仍是"没发过"，会被如实拦下。这里如实补上。
                sent_at=datetime.now(UTC),
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add(version)
            await session.flush()
            quote.current_version_id = version.id
            await session.commit()
            quote_ids.append(quote.id)
            quote_id_saved, version_id_saved = quote.id, version.id

        money_template = api(
            "POST", "/contract-templates", token=admin,
            body={
                "doc_type": "contract",
                "name": f"{MARKER}-金额",
                "body": (
                    "客户：{{customer.name}}\n"
                    "报价金额：{{quote.total_amount}}\n"
                    "报价单号：{{quote.quote_no}}\n"
                    "付款：{{quote.payment_terms}}\n"
                ),
            },
        )
        template_ids.append(money_template["id"])

        money_doc = api(
            "POST", "/contract-documents", token=zhangsan,
            body={
                "template_id": money_template["id"],
                "customer_id": own.id,
                "quote_id": quote_id_saved,
                "quote_version_id": version_id_saved,
            },
        )
        doc_ids.append(money_doc["id"])
        money_body = money_doc["content_snapshot"]
        check("正文带出了**所选那版**报价的金额（修复前这条恒为空白）",
              "12345.67" in money_body, money_body.replace("\n", " / ")[:80])
        check("{{quote.total_amount}} 不再被误判成缺项",
              "quote.total_amount" not in (money_doc["missing_fields"] or {}),
              money_doc["missing_fields"])
        check("单号挂在报价单本体上，也照样取得到",
              f"{MARKER}Q1" in money_body, money_body.split("\n")[2] if len(money_body.split("\n")) > 2 else money_body)
        check("付款条款同样来自版本", "月结30天" in money_body)
        check("台账上记住了钉死的版本号",
              money_doc.get("quote_version_no") == 2, money_doc.get("quote_version_no"))
        header = money_doc.get("header_snapshot") or {}
        check("抬头快照里存下了当时的客户名",
              header.get("customer_name") == f"{MARKER}客户", header)
        check("抬头快照里也记了报价版本号",
              header.get("quote_version_no") == 2, header)

        # 改名：台账要显示新名字（否则改名后在台账里找不到这家客户），
        # 但**原件抬头**必须还是生成时那个——两个口径故意不同。
        async with SessionLocal() as session:
            renamed = await session.get(Customer, own.id)
            renamed.name = f"{MARKER}客户改名后"
            await session.commit()

        after_rename = api("GET", f"/contract-documents/{money_doc['id']}", token=zhangsan)
        check("台账上跟当前客户名走（改名后找得到）",
              after_rename["customer_name"] == f"{MARKER}客户改名后",
              after_rename["customer_name"])
        check("但生成时的抬头快照没被改写",
              (after_rename.get("header_snapshot") or {}).get("customer_name") == f"{MARKER}客户",
              after_rename.get("header_snapshot"))

        print("=== 6. 生成稿落盘 + 校验值 ===")
        check("生成时就落了盘（generated_file_id 非空）",
              bool(money_doc.get("generated_file_id")), money_doc.get("generated_file_id"))

        async with SessionLocal() as session:
            from app.modules.file.model import FileRecord

            record = await session.get(FileRecord, money_doc["generated_file_id"])
            rec_checksum = record.checksum if record else None
            rec_size = record.size if record else 0
            rec_name = record.file_name if record else None
            rec_key = record.object_key if record else None
        check("文件记录里有 sha256 校验值（64 位）",
              rec_checksum is not None and len(rec_checksum) == 64, rec_checksum)
        check("文件记录里有大小", rec_size > 0, rec_size)
        check("文件名跟着单据编号", rec_name == f"{money_doc['doc_no']}.pdf", rec_name)

        def raw_get(path: str) -> tuple[int, bytes]:
            request = urllib.request.Request(
                f"{BASE}{path}", headers={"Authorization": f"Bearer {zhangsan}"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.read()

        status_1, bytes_1 = raw_get(f"/contract-documents/{money_doc['id']}/download")
        status_2, bytes_2 = raw_get(f"/contract-documents/{money_doc['id']}/download")
        check("下载返回的是 PDF", status_1 == 200 and bytes_1[:4] == b"%PDF",
              f"HTTP {status_1} {bytes_1[:4]}")
        check("两次下载逐字节一致（拿的是存档那一份，不是每次重渲染）",
              bytes_1 == bytes_2, f"{len(bytes_1)} vs {len(bytes_2)}")
        check("下载内容的 sha256 与落盘记录对得上",
              hashlib.sha256(bytes_1).hexdigest() == rec_checksum, rec_checksum)
        check("落盘文件确实存在磁盘上", bool(rec_key))

        print("=== 7. 登记签署前必须有正式依据（「提前备合同」口径） ===")
        no_source = api(
            "POST", "/contract-documents", token=zhangsan,
            body={"template_id": template["id"], "customer_id": own.id},
        )
        doc_ids.append(no_source["id"])
        check("没有订单也没有报价，照样能先备出草稿",
              no_source["status"] == "draft", no_source["status"])

        # 造一个"能访问的签署件"：挂在合同上，走真实的可见性判定（不能绕过它测）
        async with SessionLocal() as session:
            from app.modules.file.model import BusinessFile, FileRecord

            fixture = FileRecord(
                storage_provider="local",
                object_key=f"_fixture/{MARKER}.pdf",
                file_name=f"{MARKER}-签署件.pdf",
                mime_type="application/pdf",
                size=8,
                checksum="0" * 64,
                uploaded_by=zhangsan_id,
            )
            session.add(fixture)
            await session.flush()
            session.add(
                BusinessFile(
                    business_type="contract",
                    business_id=no_source["id"],
                    file_id=fixture.id,
                    category="signed",
                )
            )
            await session.commit()
            fixture_file_id = fixture.id

        status, result = call(
            "POST", f"/contract-documents/{no_source['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("没有正式依据 → 登记签署被拒（422）", status == 422, f"HTTP {status} {result}")
        check("拒绝理由说清了要绑什么",
              "正式" in json.dumps(result, ensure_ascii=False), result.get("message"))

        # 挂上"已发送"的报价就能签
        signable = api(
            "POST", "/contract-documents", token=zhangsan,
            body={
                "template_id": template["id"],
                "customer_id": own.id,
                "quote_id": quote_id_saved,
                "quote_version_id": version_id_saved,
            },
        )
        doc_ids.append(signable["id"])
        status, result = call(
            "POST", f"/contract-documents/{signable['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("挂上了「已发送」的报价 → 可以登记签署", status == 200, f"HTTP {status} {result}")

        print("=== 7b. 合同钉的是「没发给客户的那一版」→ 不许登记签署（复审 10.8）===")
        # 场景：一份报价改了两版，**发给客户的是 V2**，V1 从头到尾没出过门。
        # 报价单本体的状态是「已发送」——这是对的，它确实发出去过，只不过发的是 V2。
        # 但若合同是照着 V1 生成的，它依据的那一版客户从没见过：
        # 只看报价单状态就会把这份合同放行，台账上留下一个假的依据。
        async with SessionLocal() as session:
            multi_quote = Quote(
                quote_no=f"{MARKER}Q3",
                customer_id=own.id,
                owner_id=zhangsan_id,
                status="sent",
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add(multi_quote)
            await session.flush()
            never_sent = QuoteVersion(
                quote_id=multi_quote.id,
                version_no=1,
                total_amount=Decimal("100"),
                currency="CNY",
                approval_status="approved",
                # sent_at / accepted_at 都留空：**这一版**没有出过门
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            really_sent = QuoteVersion(
                quote_id=multi_quote.id,
                version_no=2,
                total_amount=Decimal("200"),
                currency="CNY",
                approval_status="approved",
                sent_at=datetime.now(UTC),  # 客户手里是这一版
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add_all([never_sent, really_sent])
            await session.flush()
            multi_quote.current_version_id = really_sent.id
            await session.commit()
            quote_ids.append(multi_quote.id)
            multi_quote_id = multi_quote.id
            never_sent_id, really_sent_id = never_sent.id, really_sent.id

        stale_doc = api(
            "POST", "/contract-documents", token=zhangsan,
            body={
                "template_id": template["id"],
                "customer_id": own.id,
                "quote_id": multi_quote_id,
                "quote_version_id": never_sent_id,
            },
        )
        doc_ids.append(stale_doc["id"])
        check("照「没发过的那一版」仍能先把合同备出来（拦在签署，不在生成）",
              stale_doc["status"] == "draft", stale_doc["status"])
        check("台账上确实钉住了那一版（V1）",
              stale_doc.get("quote_version_id") == never_sent_id,
              stale_doc.get("quote_version_id"))
        status, result = call(
            "POST", f"/contract-documents/{stale_doc['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("钉住「从没发给过客户的那一版」→ 登记签署被拒（422）",
              status == 422, f"HTTP {status} {result}")
        check("拒绝理由说清是「那一版没发过」，不是笼统的「没有依据」",
              "没有发给过客户" in json.dumps(result, ensure_ascii=False),
              result.get("message"))

        # 对照一：同一份报价，换成「客户看过的那一版」→ 照常能签。
        # 没有这条对照，上面那条红了也分不清是修对了还是把这类合同全拦死了。
        fresh_doc = api(
            "POST", "/contract-documents", token=zhangsan,
            body={
                "template_id": template["id"],
                "customer_id": own.id,
                "quote_id": multi_quote_id,
                "quote_version_id": really_sent_id,
            },
        )
        doc_ids.append(fresh_doc["id"])
        status, result = call(
            "POST", f"/contract-documents/{fresh_doc['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("钉住「客户看过的那一版」→ 照常能签（对照）",
              status == 200, f"HTTP {status} {result}")

        # 对照二：提前备合同、压根没钉版本 → 沿用老口径（整份报价是正式依据），
        # 报价单是"已发送"，放行。这条钉住"没有版本号时别误伤"。
        loose_doc = api(
            "POST", "/contract-documents", token=zhangsan,
            body={"template_id": template["id"], "customer_id": own.id,
                  "quote_id": multi_quote_id},
        )
        doc_ids.append(loose_doc["id"])
        check("没钉版本的合同确实没有版本号", loose_doc.get("quote_version_id") is None,
              loose_doc.get("quote_version_id"))
        status, result = call(
            "POST", f"/contract-documents/{loose_doc['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("没钉版本 → 沿用老口径可签（对照，证明没误伤这类合同）",
              status == 200, f"HTTP {status} {result}")

        # 草稿状态的报价不算依据：客户手里还没见过这份报价
        async with SessionLocal() as session:
            draft_quote = Quote(
                quote_no=f"{MARKER}Q2",
                customer_id=own.id,
                owner_id=zhangsan_id,
                status="draft",
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add(draft_quote)
            await session.flush()
            session.add(
                QuoteVersion(
                    quote_id=draft_quote.id,
                    version_no=1,
                    total_amount=Decimal("1"),
                    approval_status="not_submitted",
                    created_by=zhangsan_id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            quote_ids.append(draft_quote.id)
            draft_quote_id = draft_quote.id

        draft_based = api(
            "POST", "/contract-documents", token=zhangsan,
            body={"template_id": template["id"], "customer_id": own.id, "quote_id": draft_quote_id},
        )
        doc_ids.append(draft_based["id"])
        status, result = call(
            "POST", f"/contract-documents/{draft_based['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("草稿状态的报价不算正式依据（客户还没见过这份报价）",
              status == 422, f"HTTP {status} {result}")

        # 月结协议不受这条限制：它本来就是跟客户约定结算方式，未必有单
        monthly_template = api(
            "POST", "/contract-templates", token=admin,
            body={"doc_type": "monthly", "name": f"{MARKER}-月结", "body": "客户：{{customer.name}}\n"},
        )
        template_ids.append(monthly_template["id"])
        monthly_doc = api(
            "POST", "/contract-documents", token=zhangsan,
            body={"template_id": monthly_template["id"], "customer_id": own.id},
        )
        doc_ids.append(monthly_doc["id"])
        status, result = call(
            "POST", f"/contract-documents/{monthly_doc['id']}/sign",
            token=zhangsan, body={"file_id": fixture_file_id},
        )
        check("月结协议只关联客户也能登记签署（不受此限）",
              status == 200, f"HTTP {status} {result}")

        print("=== 8. 详情页按订单 / 报价筛合同（筛选不能绕过数据范围） ===")
        # 订单夹具：挂在那份「已发送」的报价版本上
        async with SessionLocal() as session:
            from app.modules.order.model import SalesOrder

            admin_id = (
                await session.execute(select(User.id).where(User.username == "admin"))
            ).scalar_one()
            order = SalesOrder(
                order_no=f"{MARKER}O1",
                customer_id=own.id,
                owner_id=zhangsan_id,
                sales_owner_id=zhangsan_id,
                total_amount=Decimal("12345.67"),
                status="pending",
                quote_id=quote_id_saved,
                quote_version_id=version_id_saved,
            )
            session.add(order)
            await session.flush()
            await session.commit()
            order_ids.append(order.id)
            order_id_saved = order.id

        # 只传订单、不传报价版本：后端要按订单依据的那一版自动带出，
        # 否则从订单进详情页生成合同的人还得自己去翻是 V1 还是 V2
        by_order = api(
            "POST", "/contract-documents", token=zhangsan,
            body={
                "template_id": money_template["id"],
                "customer_id": own.id,
                "order_id": order_id_saved,
            },
        )
        doc_ids.append(by_order["id"])
        check("选了订单，报价版本自动带出（不必再手选一次）",
              by_order.get("quote_version_no") == 2, by_order.get("quote_version_no"))

        scoped_order = api(
            "GET", f"/contract-documents?order_id={order_id_saved}&page_size=50", token=zhangsan
        )
        check("按订单筛只返回挂在这一单下面的合同",
              [d["id"] for d in scoped_order["items"]] == [by_order["id"]],
              [d["doc_no"] for d in scoped_order["items"]])
        check("按订单筛不会把同客户其它合同一起带出来",
              all(d.get("order_id") == order_id_saved for d in scoped_order["items"]),
              [(d["doc_no"], d.get("order_id")) for d in scoped_order["items"]])

        scoped_quote = api(
            "GET", f"/contract-documents?quote_id={quote_id_saved}&page_size=50", token=zhangsan
        )
        quote_hits = {d["id"] for d in scoped_quote["items"]}
        # 注意：只传订单生成的那份（by_order）也会出现在结果里，而且**这是对的**——
        # 后端会由订单依据的版本反推出报价单（`由版本反推报价单`那段），
        # 所以这份合同确实"挂在这份报价上"。两个维度查出来的集合本来就不同：
        # 按订单 = 只有这一单发的；按报价 = 所有依据这一版报价的（可能来自好几张单）。
        check("按报价筛能查到所有依据这份报价的合同（含从订单带出来的）",
              {money_doc["id"], signable["id"], by_order["id"]} <= quote_hits, sorted(quote_hits))
        check("按报价筛不会带出根本没挂这份报价的合同",
              doc["id"] not in quote_hits and no_source["id"] not in quote_hits, sorted(quote_hits))

        # 越权：新增的筛选参数最容易出的漏洞就是"绕过了数据范围"——
        # 拿一个别人的 order_id，本该查不到，结果筛选条件把范围条件覆盖掉了。
        async with SessionLocal() as session:
            from app.modules.order.model import SalesOrder

            outsider = Customer(name=f"{MARKER}他人客户", owner_id=admin_id)
            session.add(outsider)
            await session.flush()
            customer_ids.append(outsider.id)
            outsider_order = SalesOrder(
                order_no=f"{MARKER}O2",
                customer_id=outsider.id,
                owner_id=admin_id,
                sales_owner_id=admin_id,
                status="pending",
            )
            session.add(outsider_order)
            await session.flush()
            await session.commit()
            order_ids.append(outsider_order.id)
            outsider_customer_id, outsider_order_id = outsider.id, outsider_order.id

        outsider_doc = api(
            "POST", "/contract-documents", token=admin,
            body={
                "template_id": template["id"],
                "customer_id": outsider_customer_id,
                "order_id": outsider_order_id,
            },
        )
        doc_ids.append(outsider_doc["id"])
        mine = api(
            "GET", f"/contract-documents?order_id={outsider_order_id}&page_size=50", token=admin
        )
        check("管理员按该订单查得到（说明夹具本身是有效的）",
              [d["id"] for d in mine["items"]] == [outsider_doc["id"]],
              [d["doc_no"] for d in mine["items"]])

        status, peek = call(
            "GET", f"/contract-documents?order_id={outsider_order_id}&page_size=50", token=zhangsan
        )
        leaked = status == 200 and bool((peek.get("data") or {}).get("items"))
        check("换个人按同一订单查，看不到别家的合同（筛选没绕过数据范围）",
              not leaked, f"HTTP {status} {json.dumps(peek, ensure_ascii=False)[:90]}")

    finally:
        async with SessionLocal() as session:
            from app.modules.file import storage as _storage
            from app.modules.file.model import BusinessFile as _BusinessFile
            from app.modules.file.model import FileRecord as _FileRecord
            from app.modules.quote.model import Quote as _Quote
            from app.modules.quote.model import QuoteVersion as _QuoteVersion
            from app.modules.order.model import SalesOrder as _SalesOrder

            # 这一批会生成真实落盘的 PDF（生成稿）和一份假的签署件夹具，
            # 文件记录 + 磁盘文件都要收掉，否则每跑一次留一堆孤儿文件。
            if doc_ids:
                file_ids = (
                    await session.execute(
                        select(_BusinessFile.file_id).where(
                            _BusinessFile.business_type == "contract",
                            _BusinessFile.business_id.in_(doc_ids),
                        )
                    )
                ).scalars().all()
                if file_ids:
                    records = (
                        await session.execute(select(_FileRecord).where(_FileRecord.id.in_(file_ids)))
                    ).scalars().all()
                    for record in records:
                        try:
                            _storage.delete_object(record.object_key)
                        except Exception:  # noqa: BLE001 —— 清理阶段不该因一个文件失败中断
                            pass
                    await session.execute(
                        delete(_BusinessFile).where(_BusinessFile.file_id.in_(file_ids))
                    )
                    await session.execute(delete(_FileRecord).where(_FileRecord.id.in_(file_ids)))
            if doc_ids:
                await session.execute(delete(ContractDocument).where(ContractDocument.id.in_(doc_ids)))
            if order_ids:
                # 顺序：合同 → 订单 → 客户。合同的 order_id 是外键，必须在订单之前删。
                await session.execute(delete(_SalesOrder).where(_SalesOrder.id.in_(order_ids)))
            if template_ids:
                await session.execute(delete(ContractTemplate).where(ContractTemplate.id.in_(template_ids)))
            if quote_ids:
                # 报价版本先删（外键指向 quotes），再删报价单；两者都在删客户之前
                await session.execute(
                    delete(_QuoteVersion).where(_QuoteVersion.quote_id.in_(quote_ids))
                )
                await session.execute(delete(_Quote).where(_Quote.id.in_(quote_ids)))
            if customer_ids:
                await session.execute(delete(FollowUp).where(FollowUp.customer_id.in_(customer_ids)))
                await session.execute(delete(BusinessEvent).where(BusinessEvent.customer_id.in_(customer_ids)))
                await session.execute(delete(Notification).where(Notification.content.contains(MARKER)))
                await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            await session.execute(
                delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
            )
            # **必须显式提交**：`async with SessionLocal()` 退出时只 close，
            # 没提交的事务会整体回滚 —— 上面那些 delete 就全白写了。
            # 这个套件第一次写完就栽在这里：守门套件当场报「客户残留 2 条」。
            await session.commit()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 合同模板缺项、生成防重、台账分页、作废原因、"
        "报价版本取值与抬头快照、生成稿落盘、签署依据校验、详情页按订单/报价筛选"
    )


if __name__ == "__main__":
    asyncio.run(main())
