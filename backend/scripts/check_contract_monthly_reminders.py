"""月结到期提醒 / 客户交接 / 客户事件（外部审查第二批 阶段 C 的回归）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

覆盖 6 处：

1. **提醒按「协议 + 到期周期」去重**，不再靠标题。改前是 `title == ... AND status IN
   (pending, doing)`：任务一完成，下次扫描找不到它，又建一条同样的待办。
2. **任务完成后不自动重建**（业务口径 2026-10-05：要接着办就重新打开原任务）。
3. **协议作废 → 在办的到期待办一并结束**；之后再扫也不会新建。
4. **续签勾「替代旧协议」→ 旧协议的在办待办结束**，且不改旧协议的状态、不动作废。
5. **客户交接 → 未完成的待办跟着新负责人走**，已完成的不动（保留原处理人）。
6. **签署 / 作废进客户时间线**（复用已有的 followup 留痕机制，不另起一套）。

跑法（隔离库；不要对着默认开发库跑，`check_*` 会清库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \
      PYTHONPATH=. .venv/bin/python scripts/check_contract_monthly_reminders.py
"""

import asyncio
import os
import time
from datetime import date, timedelta
from urllib.parse import urlparse

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.contract.service import notify_expiring_monthly
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.task.model import Task
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKREM{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
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

    admin = login("admin", "admin123")
    zhangsan = login("zhangsan", "123456")
    customer_ids: list[int] = []
    doc_ids: list[int] = []
    template_ids: list[int] = []
    task_ids: list[int] = []
    file_ids: list[int] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    async def scan() -> int:
        """跑一次到期扫描（这是调度任务，没有接口，直接调服务）。"""
        async with SessionLocal() as session:
            created = await notify_expiring_monthly(session)
            await session.commit()
            return created

    async def make_monthly(expiry: date, *, parent_id: int | None = None,
                           supersede: bool = False) -> dict:
        """建一份月结协议并登记签署（签署要真实走接口，别直接改状态）。"""
        doc = api(
            "POST", "/contract-documents", token=admin,
            body={
                "template_id": monthly_template_id,
                "customer_id": customer_id,
                "expiry_date": expiry.isoformat(),
                **({"parent_id": parent_id} if parent_id else {}),
                **({"supersede_parent": True} if supersede else {}),
            },
        )
        doc_ids.append(doc["id"])
        async with SessionLocal() as session:
            from app.modules.file.model import BusinessFile, FileRecord

            record = FileRecord(
                storage_provider="local",
                object_key=f"_fixture/{MARKER}-{doc['id']}.pdf",
                file_name=f"{MARKER}-{doc['id']}.pdf",
                mime_type="application/pdf",
                size=8,
                checksum="0" * 64,
                uploaded_by=zhangsan_id,
            )
            session.add(record)
            await session.flush()
            session.add(
                BusinessFile(
                    business_type="contract",
                    business_id=doc["id"],
                    file_id=record.id,
                    category="signed",
                )
            )
            await session.commit()
            file_ids.append(record.id)
            sign_file_id = record.id
        api("POST", f"/contract-documents/{doc['id']}/sign",
            body={"file_id": sign_file_id}, token=zhangsan)
        return doc

    def find_task(doc_id: int) -> dict | None:
        rows = api("GET", f"/tasks?customer_id={customer_id}&page_size=100", token=admin)["items"]
        for row in rows:
            if row.get("source_business_type") == "contract" and row.get("source_business_id") == doc_id:
                return row
        return None

    try:
        async with SessionLocal() as session:
            zhangsan_id = (
                await session.execute(select(User.id).where(User.username == "zhangsan"))
            ).scalar_one()
            lisi_id = (
                await session.execute(select(User.id).where(User.username == "lisi"))
            ).scalar_one()
            own = Customer(name=f"{MARKER}客户", owner_id=zhangsan_id)
            session.add(own)
            await session.flush()
            customer_ids.append(own.id)
            await session.commit()
            customer_id = own.id

        template = api("POST", "/contract-templates", token=admin, body={
            "doc_type": "monthly", "name": f"{MARKER}月结", "body": "客户：{{customer.name}}\n",
        })
        template_ids.append(template["id"])
        monthly_template_id = template["id"]

        soon = date.today() + timedelta(days=10)

        print("=== 1. 到期提醒按「协议」去重，不再靠标题 ===")
        doc_a = await make_monthly(soon)
        check("第一次扫描建出 1 条到期待办", await scan() == 1)
        check("紧接着再扫一次不会重复建（以前靠标题会漏判）", await scan() == 0)

        task_a = find_task(doc_a["id"])
        check("待办上带了来源单据号（界面能显示是哪份协议）",
              bool(task_a) and task_a.get("source_doc_no") == doc_a["doc_no"],
              f"{task_a.get('source_doc_no') if task_a else None} vs {doc_a['doc_no']}")
        check("待办上也带了来源类型/ID（点得进具体协议）",
              bool(task_a) and task_a.get("source_business_type") == "contract"
              and task_a.get("source_business_id") == doc_a["id"], task_a)
        if task_a:
            task_ids.append(task_a["id"])

        print("=== 2. 任务完成后不自动重建（业务口径） ===")
        api("POST", f"/tasks/{task_a['id']}/complete", token=admin,
            body={"completion_note": f"{MARKER} 已处理"})
        check("完成之后再次扫描不会又冒出一条", await scan() == 0)
        done = api("GET", f"/tasks/{task_a['id']}", token=admin)
        check("那条待办仍是「已完成」而不是被新的一条顶掉",
              done["status"] == "done", done["status"])

        print("=== 3. 协议作废 → 在办待办一并结束 ===")
        doc_b = await make_monthly(soon + timedelta(days=1))
        check("第二份协议的提醒也建出来了", await scan() == 1)
        task_b = find_task(doc_b["id"])
        api("POST", f"/contract-documents/{doc_b['id']}/void", token=admin,
            body={"reason": f"{MARKER} 客户终止月结"})
        after_void = api("GET", f"/tasks/{task_b['id']}", token=admin)
        check("协议作废后，它在办的到期待办被结束",
              after_void["status"] == "cancelled", after_void["status"])
        check("作废之后再扫描不会为它新建提醒", await scan() == 0)

        print("=== 4. 续签勾「替代旧协议」→ 旧协议的在办待办结束 ===")
        doc_e = await make_monthly(soon + timedelta(days=2))
        check("第三份协议的提醒建出来了", await scan() == 1)
        task_e = find_task(doc_e["id"])
        check("此刻旧协议有一条在办提醒", task_e["status"] == "pending", task_e["status"])
        doc_f = await make_monthly(soon + timedelta(days=400), parent_id=doc_e["id"], supersede=True)
        check("续签文档挂在了原协议下面（关系链可查）", doc_f.get("parent_id") == doc_e["id"])
        task_e_after = api("GET", f"/tasks/{task_e['id']}", token=admin)
        check("勾了替代：旧协议的在办待办被结束",
              task_e_after["status"] == "cancelled", task_e_after["status"])
        old_doc = api("GET", f"/contract-documents/{doc_e['id']}", token=admin)
        check("但旧协议本身**不改状态**（替代不等于作废）",
              old_doc["status"] == "signed", old_doc["status"])
        check("旧协议上留了「被谁替代」的痕迹",
              ((old_doc.get("filled_data") or {}).get("_superseded_by") or {}).get("doc_no")
              == doc_f["doc_no"], (old_doc.get("filled_data") or {}).get("_superseded_by"))

        print("=== 5. 客户交接 → 待办责任跟着走 ===")
        manual = api("POST", "/tasks", token=admin, body={
            "title": f"{MARKER} 手工待办", "customer_id": customer_id, "owner_id": zhangsan_id,
        })
        task_ids.append(manual["id"])
        api("POST", f"/customers/{customer_id}/transfer", token=admin,
            body={"owner_id": lisi_id, "reason": f"{MARKER} 交接"})
        moved = api("GET", f"/tasks/{manual['id']}", token=admin)
        check("未完成的待办跟着新负责人走了",
              moved["owner_id"] == lisi_id, moved["owner_id"])
        still_done = api("GET", f"/tasks/{task_a['id']}", token=admin)
        check("已完成的待办**不跟着走**（历史记录保留原处理人）",
              still_done["owner_id"] == zhangsan_id, still_done["owner_id"])

        print("=== 6. 签署 / 作废进客户时间线 ===")
        timeline = api("GET", f"/customers/{customer_id}/timeline?limit=200", token=admin)
        details = " ".join(str(row.get("detail") or "") for row in timeline)
        check("客户时间线里能看到「已登记签署」",
              doc_a["doc_no"] in details and "签署" in details,
              details[:80])
        check("客户时间线里能看到「已作废」（含原因）",
              doc_b["doc_no"] in details and "作废" in details, details[:80])
        check("续签也留了痕", doc_f["doc_no"] in details, details[:80])

    finally:
        async with SessionLocal() as session:
            from app.modules.file import storage as _storage
            from app.modules.file.model import BusinessFile as _BusinessFile
            from app.modules.file.model import FileRecord as _FileRecord

            if file_ids:
                keys = (
                    await session.execute(
                        select(_FileRecord.object_key).where(_FileRecord.id.in_(file_ids))
                    )
                ).scalars().all()
                await session.execute(
                    delete(_BusinessFile).where(_BusinessFile.file_id.in_(file_ids))
                )
                await session.execute(delete(_FileRecord).where(_FileRecord.id.in_(file_ids)))
                for key in keys:
                    try:
                        _storage.delete_object(key)
                    except Exception:  # noqa: BLE001 —— 清理阶段不该因一个文件失败中断
                        pass
            if customer_ids:
                await session.execute(delete(Task).where(Task.customer_id.in_(customer_ids)))
            if doc_ids:
                await session.execute(delete(ContractDocument).where(ContractDocument.id.in_(doc_ids)))
            if template_ids:
                await session.execute(delete(ContractTemplate).where(ContractTemplate.id.in_(template_ids)))
            if customer_ids:
                from app.modules.customer.model import CustomerOwnerHistory

                # 客户交接会写一条负责人变更历史，它外键指向 customers——
                # 忘了这张表，删客户就会 ForeignKeyViolationError（本轮实测栽过一次）。
                await session.execute(
                    delete(CustomerOwnerHistory).where(
                        CustomerOwnerHistory.customer_id.in_(customer_ids)
                    )
                )
                await session.execute(delete(FollowUp).where(FollowUp.customer_id.in_(customer_ids)))
                await session.execute(delete(BusinessEvent).where(BusinessEvent.customer_id.in_(customer_ids)))
                await session.execute(delete(Notification).where(Notification.content.contains(MARKER)))
                await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            await session.execute(
                delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
            )
            # **必须显式提交**：`async with SessionLocal()` 退出时只 close，
            # 没提交的事务整体回滚 —— 上面那些 delete 就全白写了。
            await session.commit()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print("\nOK 月结提醒去重、完成后不重建、作废/续签结束待办、交接转移、客户时间线")


if __name__ == "__main__":
    asyncio.run(main())
