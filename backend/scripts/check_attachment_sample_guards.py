"""附件可见性 / 删除保护 + 打样入参 + 批次节点一致性（2026-10-05 修复的回归）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。
不触达邮件、钉钉、企微、ERP、AI。

覆盖这一轮修的 6 处：

1. 附件解绑：有权限可解绑、他人业务对象上的关联被拒、关联不存在 404。
   修复前该接口把**只能按名字传**的参数按位置传，**每次调用必 500**；返回值也没判。
2. 询价附件类型已登记：**走真实上传接口**能挂到询价；挂不上别人的询价。
   修复前 `inquiry` 未登记 → 上游钉钉 OA 要的「产品参考图片」永远取不到。
3. 删除保护：已签署原件、被多个对象引用的文件，通用删除被拒。
4. 打样数量/费用：旧入口 0 / 负数被拒；费用负数被拒；清空 = 未填（存 NULL），与 0 元区分。
5. 制作完成重试去重：带说明重复提交只记一次，过程记录不重复。
6. 批次节点：动态批次节点的计划日 / 实际日不能单独改；已登记实际完成的节点不随批次取消被删。
7. 车间依据闸门（材质/工艺/图纸版本/目标完成日/验收标准）：
   已批准的单子改了（含**加明细**）→ 退回「待审批」重批；已寄样/已签收 → 直接拒；
   明细接口不收无关字段（不静默忽略）。
8. 驳回可重提：驳回**不是终态**，已驳回有三条路 ——
   ① 改任何一项资料（含费用这类非车间依据的字段）自动回「待审批」；
   ② 一个字都不改，走 `/samples/{id}/resubmit` 原样再报一次；
   ③ 主管直接「改判为批准」（上一次驳错了）。同值重发不算改动。
9. 打样附件锁 + 制作依据（2026-10-06 补，审视第 1 条）：
   已制作/已寄出的打样单，其附件**不能解绑、不能删除**，也不能再挂"依据类"附件
   （这两条此前都能从通用附件入口穿过去——原件保护只覆盖 signed/generated）；
   「后续补充资料」（`category=supplement`）仍可上传；
   登记制作完成时可**显式指定制作依据**，快照里带 sha256，登记即固化；
   拿别的打样单上的文件当依据会被拒。

跑法（隔离库；不要对着默认开发库跑，`check_*` 会清库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \
      PYTHONPATH=. .venv/bin/python scripts/check_attachment_sample_guards.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, func, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.customer.model import Customer
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file import storage
from app.modules.followup.model import FollowUp
from app.modules.inquiry.model import CustomInquiry
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.order.model import (
    OrderMilestone,
    OrderScheduleChange,
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    OrderStatusHistory,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.product.model import Sku
from app.modules.sample.model import SampleItem, SampleRequest, SampleShipment
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKGUARD{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def rejected(status: int) -> bool:
    """入参非法：接口可能回 400（参数错误）或 422（业务规则拒绝），都算拒绝。"""
    return status in (400, 422)


def upload(token: str, filename: str, *, business_type=None, business_id=None, category=None):
    """走**真实上传接口**。

    刻意不直接往 `business_files` 插行——那正是这轮要堵的缺口
    （`check_dingtalk_contract.py` 就是这么绕过去、所以没发现询价类型挂不上）。
    """
    boundary = "----chkguard" + uuid4().hex
    chunks: list[bytes] = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'
        ).encode(),
        b"\x89PNG\r\n\x1a\n" + os.urandom(32),
        b"\r\n",
    ]
    for name, value in (
        ("business_type", business_type),
        ("business_id", business_id),
        ("category", category),
    ):
        if value is not None:
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{BASE}/files/upload",
        data=b"".join(chunks),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, (json.loads(raw) if raw else {})


async def main():
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}; assert "test" in db.path.lower() or os.getenv("CI") == "true", db
    assert settings.wecom_push_off and settings.dingtalk_push_off and not settings.scheduler_enabled, (
        "这条回归只能在推送全关的隔离库跑"
    )

    admin = login("admin", "admin123")
    zhangsan = login("zhangsan", "123456")
    customer_ids: list[int] = []
    file_ids: list[int] = []
    sample_ids: list[int] = []
    order_ids: list[int] = []
    doc_ids: list[int] = []
    template_ids: list[int] = []
    quote_ids: list[int] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        async with SessionLocal() as session:
            zhangsan_id = (await session.execute(select(User.id).where(User.username == "zhangsan"))).scalar_one()
            admin_id = (await session.execute(select(User.id).where(User.username == "admin"))).scalar_one()
            sku_id = (await session.execute(select(Sku.id).limit(1))).scalar_one()
            own = Customer(name=f"{MARKER}自己", owner_id=zhangsan_id)
            # 「别人」的负责人必须是业务员看不到的人。负责人留空的话，
            # 询价的可见性规则把 owner_id 为空也算可见，就测不出越权了。
            other = Customer(name=f"{MARKER}他人", owner_id=admin_id)
            session.add_all([own, other])
            await session.flush()
            customer_ids += [own.id, other.id]
            own_inquiry = CustomInquiry(
                customer_id=own.id, title=MARKER, inquiry_no=f"{MARKER}-A", quantity=100, created_by=zhangsan_id
            )
            other_inquiry = CustomInquiry(
                customer_id=other.id, title=f"{MARKER}他人", inquiry_no=f"{MARKER}-B", quantity=100, created_by=admin_id
            )
            session.add_all([own_inquiry, other_inquiry])
            await session.flush()
            own_iid, other_iid = own_inquiry.id, other_inquiry.id
            await session.commit()

        print("=== 1. 附件解绑（修复前每次必 500） ===")
        status, result = upload(zhangsan, "own.png", business_type="customer", business_id=own.id)
        assert status == 200, result
        own_file = result["data"]["id"]
        file_ids.append(own_file)
        rows = api("GET", f"/business/customer/{own.id}/files", token=zhangsan)
        own_link = rows[0]["business_file_id"]
        api("DELETE", f"/business-files/{own_link}", token=zhangsan)
        check("自己业务对象上的附件可以解绑", True)
        check("解绑后文件本体仍在", isinstance(api("GET", f"/files/{own_file}", token=zhangsan), dict))

        status, result = upload(admin, "foreign.png", business_type="customer", business_id=other.id)
        assert status == 200, result
        foreign_file = result["data"]["id"]
        file_ids.append(foreign_file)
        foreign_link = api("GET", f"/business/customer/{other.id}/files", token=admin)[0]["business_file_id"]
        status, result = call("DELETE", f"/business-files/{foreign_link}", token=zhangsan)
        check("他人业务对象上的关联不能解绑", status == 403, f"HTTP {status} {result}")
        status, result = call("DELETE", "/business-files/999999999", token=zhangsan)
        check("不存在的关联返回 404", status == 404, f"HTTP {status}")

        print("=== 2. 询价附件类型已登记（走真实上传） ===")
        status, result = upload(zhangsan, "drawing.png", business_type="inquiry", business_id=own_iid)
        check("询价能走真实上传接口挂上附件", status == 200, f"HTTP {status} {result}")
        if status == 200:
            file_ids.append(result["data"]["id"])
            check("询价附件清单可查", len(api("GET", f"/business/inquiry/{own_iid}/files", token=zhangsan)) == 1)
        status, result = upload(zhangsan, "x.png", business_type="inquiry", business_id=other_iid)
        check("挂不上别人的询价", status == 403, f"HTTP {status}")
        status, result = call("GET", f"/business/inquiry/{other_iid}/files", token=zhangsan)
        check("看不到别人询价的附件清单", status == 403, f"HTTP {status}")

        print("=== 3. 删除保护 ===")
        multi = upload(zhangsan, "multi.png", business_type="customer", business_id=own.id)[1]["data"]["id"]
        file_ids.append(multi)
        api("POST", f"/business/customer/{other.id}/files?file_id={multi}", token=admin)
        status, result = call("DELETE", f"/files/{multi}", token=admin)
        check("被多个对象引用的文件不能直接删", status == 422, f"HTTP {status} {result}")

        template = api("POST", "/contract-templates", body={"doc_type": "contract", "name": f"{MARKER}模板", "body": "甲方：{{party}}"}, token=admin)
        template_ids.append(template["id"])
        # 登记签署现在要求有正式依据（条款可先备，签署前必须挂上订单或**已发送/已接受**的报价）。
        # 这一段测的是"签署件不能删 / 挂载类型 / 已签作废要主管"，不该被依据校验挡住，
        # 所以先造一份「已发送」的报价当依据。
        async with SessionLocal() as session:
            from datetime import UTC, datetime

            from app.modules.quote.model import Quote

            basis_quote = Quote(
                quote_no=f"{MARKER}Q",
                customer_id=own.id,
                owner_id=zhangsan_id,
                status="sent",
                created_by=zhangsan_id,
                created_at=datetime.now(UTC),
            )
            session.add(basis_quote)
            await session.commit()
            basis_quote_id = basis_quote.id
        quote_ids.append(basis_quote_id)
        doc = api("POST", "/contract-documents",
                  body={"template_id": template["id"], "customer_id": own.id,
                        "quote_id": basis_quote_id},
                  token=zhangsan)
        doc_ids.append(doc["id"])
        signed = upload(zhangsan, "signed.pdf", business_type="contract", business_id=doc["id"], category="signed")[1]["data"]["id"]
        file_ids.append(signed)
        api("POST", f"/contract-documents/{doc['id']}/sign", body={"file_id": signed}, token=zhangsan)
        status, result = call("DELETE", f"/files/{signed}", token=admin)
        check("已签署的原件不能被通用删除", status == 422, f"HTTP {status} {result}")

        # 签署件的挂载类型：后端挂的是 `contract`。前端原来按 `contract_document` 查，
        # 而权限表对**没登记的类型默认拒绝** → 那个下拉永远 403、一个文件都列不出来
        # （后端日志里实测到过这条 403）。所以前端必须改用 contract。
        rows = api("GET", f"/business/contract/{doc['id']}/files", token=zhangsan)
        # 注意字段：行里的 `id` 是文件本身（FileRecord.id），`business_file_id` 是那条关联的编号
        check("按 contract 类型能查到刚挂上的签署件",
              any(r.get("id") == signed for r in rows), rows)
        status, result = call("GET", f"/business/contract_document/{doc['id']}/files", token=zhangsan)
        check("按 contract_document 查仍然被拒（前端必须用 contract）",
              status == 403, f"HTTP {status}")

        # 解绑同样是一条绕过路径（第一批返修 §3.1）：先把签署件从合同上解绑，文件就不再
        # 挂任何业务对象，而 `can_access_file` 对无关联文件**只认上传者** ——
        # 上传者随后就能把它删掉，"已签原件不可删"这条保护就白设了。
        # 所以删除要拦、解绑也要拦，而且失败路径不能改动关联状态。
        signed_link = next(
            (r.get("business_file_id") for r in rows if r.get("id") == signed), None
        )
        check("找得到签署件那条关联（下面的断言才有意义）", signed_link is not None, True)
        if signed_link is not None:
            status, result = call("DELETE", f"/business-files/{signed_link}", token=admin)
            check("已签署的原件不能被解绑（否则上传者可先解绑再删除）",
                  status == 422, f"HTTP {status} {result}")
            rows_after = api("GET", f"/business/contract/{doc['id']}/files", token=zhangsan)
            check("被拒之后关联还在（失败路径不改状态）",
                  any(r.get("business_file_id") == signed_link for r in rows_after), True)

        # 已签合同的作废要主管（业务方 2026-10-05 定）
        status, result = call("POST", f"/contract-documents/{doc['id']}/void",
                              token=zhangsan, body={"reason": f"{MARKER} 业务员想作废"})
        check("普通业务员作废不了已签合同", status == 403, f"HTTP {status} {result}")
        after = api("GET", f"/contract-documents/{doc['id']}", token=zhangsan)
        check("被拒之后状态没被动过", after["status"] == "signed", after["status"])
        status, result = call("POST", f"/contract-documents/{doc['id']}/void",
                              token=admin, body={"reason": f"{MARKER} 主管确认误签"})
        check("主管可以作废已签合同", status == 200, f"HTTP {status} {result}")

        print("=== 4. 打样数量 / 费用校验 ===")
        sample = api("POST", "/samples", body={"customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "12"}]}, token=zhangsan)
        sid = sample["id"]
        sample_ids.append(sid)
        status, result = call("POST", "/samples", token=zhangsan, body={"customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "0"}]})
        check("旧入口数量 0 被拒", rejected(status), f"HTTP {status}")
        status, result = call("POST", f"/samples/{sid}/items", token=zhangsan, body={"sku_id": sku_id, "quantity": "-1"})
        check("追加明细数量为负被拒", rejected(status), f"HTTP {status}")
        status, result = call("PATCH", f"/samples/{sid}", token=zhangsan, body={"sample_fee": "-5"})
        check("费用为负被拒", rejected(status), f"HTTP {status}")
        api("PATCH", f"/samples/{sid}", body={"sample_fee": None}, token=zhangsan)
        cleared = api("GET", f"/samples/{sid}", token=zhangsan)
        check("清空费用 = 未填（不再撞数据库约束）", cleared["sample_fee"] is None, cleared["sample_fee"])
        api("PATCH", f"/samples/{sid}", body={"sample_fee": "0"}, token=zhangsan)
        zero = api("GET", f"/samples/{sid}", token=zhangsan)
        check("0 元与未填是两个状态", zero["sample_fee"] == 0, zero["sample_fee"])

        print("=== 5. 制作完成重试去重 ===")
        api("POST", f"/samples/{sid}/approve", body={"approved": True}, token=zhangsan)
        _, first = call("POST", f"/samples/{sid}/made", token=zhangsan, body={"remark": "车间已完成"})
        _, second = call("POST", f"/samples/{sid}/made", token=zhangsan, body={"remark": "车间已完成"})
        check("同内容重发被识别为没有变化", "没有变化" in (second.get("message") or ""), second.get("message"))
        async with SessionLocal() as session:
            # 自动留痕没有标题列：写的是 followup_type="系统"、正文以「【系统】」开头，
            # 所以按正文里的「制作完成」认。重发若没被去重，这里会数到 2 条。
            made_events = (await session.execute(
                select(func.count()).select_from(FollowUp).where(
                    FollowUp.sample_id == sid, FollowUp.content.contains("制作完成")
                )
            )).scalar_one()
        check("过程记录只留一条", made_events == 1, made_events)

        print("=== 6. 批次与跟单节点一致性 ===")
        order_id = api("POST", "/orders", body={"customer_id": own.id, "delivery_date": "2026-12-01",
                        "items": [{"sku_id": sku_id, "quantity": 100, "unit_price": 10}]}, token=admin)["order_id"]
        order_ids.append(order_id)
        item = api("GET", f"/orders/{order_id}/shipments", token=admin)["items"][0]["order_item_id"]
        api("POST", f"/orders/{order_id}/shipments", body={"planned_date": "2026-11-20", "items": [{"order_item_id": item, "planned_qty": 40}]}, token=admin)
        batch2 = api("POST", f"/orders/{order_id}/shipments", body={"planned_date": "2026-11-25", "items": [{"order_item_id": item, "planned_qty": 60}]}, token=admin)["batch_id"]
        nodes = {n["node"]: n for n in api("GET", f"/orders/{order_id}/milestones", token=admin)}
        node = nodes.get("shipment_batch_2")
        check("第 2 批自动生成了跟单节点", node is not None)
        if node:
            path = f"/orders/{order_id}/milestones/{node['id']}"
            status, result = call("PATCH", path, token=admin, body={"planned_date": "2026-11-30"})
            check("批次节点的计划日不能单独改", status == 422, f"HTTP {status}")
            status, result = call("PATCH", path, token=admin, body={"actual_date": "2026-11-25"})
            check("批次节点的实际日不能单独改", status == 422, f"HTTP {status}")
            status, result = call("PATCH", path, token=admin, body={"remark": "车间说延一天"})
            check("备注仍然可以改", status == 200, f"HTTP {status}")
            async with SessionLocal() as session:
                row = await session.get(OrderMilestone, node["id"])
                row.actual_date = datetime.now(UTC).date()
                await session.commit()
            status, result = call("DELETE", f"/orders/{order_id}/shipments/{batch2}", token=admin)
            check("已登记实际完成的批次节点不随批次取消被删",
                  status == 422 and "实际发货" in json.dumps(result, ensure_ascii=False), f"HTTP {status} {result}")

        print("=== 7. 车间依据闸门：已批准未制作→退回重审；已制作/已寄出→只能开修订版 ===")
        # ① 「已批准 + **未**制作」：允许原地改，但那一版批准作废 → 退回待审批。
        #    另造一张来测这档，别动 sid —— 它已经制作完成（见 ②）。
        gate = api("POST", "/samples", body={
            "customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "2"}]},
            token=zhangsan)
        gate_id = gate["id"]
        sample_ids.append(gate_id)
        gate_item = api("GET", f"/samples/{gate_id}/items", token=zhangsan)[0]["id"]
        api("POST", f"/samples/{gate_id}/approve", body={"approved": True}, token=zhangsan)
        after = api(
            "PATCH", f"/samples/{gate_id}/items/{gate_item}", token=zhangsan,
            body={"material": f"{MARKER} 304不锈钢"},
        )
        check("已批准（未制作）改明细车间依据 → 退回待审批",
              after["status"] == "pending", after["status"])
        check("退回后批准时间被清掉（那一版批准已作废）",
              after["approved_at"] is None, after["approved_at"])
        check("改动确实落库了（不是只退状态）",
              after["items"][0]["material"] == f"{MARKER} 304不锈钢")

        api("POST", f"/samples/{gate_id}/approve", body={"approved": True}, token=zhangsan)
        after = api(
            "PATCH", f"/samples/{gate_id}", token=zhangsan,
            body={"target_completion_date": "2026-12-31"},
        )
        check("已批准（未制作）改单头交期 → 同样退回待审批",
              after["status"] == "pending", after["status"])

        # ② sid 此刻是「已批准 + **已登记制作完成**」：必须封死原地改（§3.3 口径 A）。
        #    车间可能已经开工，改材质等于让它按老要求白干；而且同一行上留着旧制作时间
        #    却写着新资料，和已经做出来的实物对不上。出口是开新修订版（原版冻结保留）。
        item_id = api("GET", f"/samples/{sid}/items", token=zhangsan)[0]["id"]
        status, result = call(
            "PATCH", f"/samples/{sid}/items/{item_id}", token=zhangsan,
            body={"material": f"{MARKER} 304不锈钢"},
        )
        check("已制作后原地改明细车间依据被拒（要开修订版）",
              status == 422, f"HTTP {status} {result}")
        status, result = call(
            "PATCH", f"/samples/{sid}", token=zhangsan,
            body={"acceptance_criteria": "改验收标准"},
        )
        check("已制作后原地改单头验收标准被拒", status == 422, f"HTTP {status}")

        # ③ 已寄样同样封死：货都在客户手上了，"退回待审批"更荒唐（货都到了）
        api("POST", f"/samples/{sid}/ship",
            body={"carrier": "顺丰", "tracking_no": f"SF{MARKER}"}, token=zhangsan)
        status, result = call(
            "PATCH", f"/samples/{sid}/items/{item_id}", token=zhangsan, body={"material": "换料"}
        )
        check("已寄样后改明细车间依据被拒", status == 422, f"HTTP {status} {result}")
        status, result = call(
            "PATCH", f"/samples/{sid}", token=zhangsan, body={"acceptance_criteria": "改验收标准"}
        )
        check("已寄样后改验收标准被拒", status == 422, f"HTTP {status}")

        # 还没批的单子不受影响：本来就没有"已批准的版本"要作废
        fresh = api("POST", "/samples", body={"customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "1"}]}, token=zhangsan)
        fresh_id = fresh["id"]
        sample_ids.append(fresh_id)
        fresh_item = api("GET", f"/samples/{fresh_id}/items", token=zhangsan)[0]["id"]
        after = api(
            "PATCH", f"/samples/{fresh_id}/items/{fresh_item}", token=zhangsan, body={"craft": "模切+粘箱"}
        )
        check("待审批的单子改车间依据照常放行", after["status"] == "pending", after["status"])

        # 窄接口：多传字段必须明确报错，不能静默丢掉（否则前端以为改成功了）
        status, result = call(
            "PATCH", f"/samples/{fresh_id}/items/{fresh_item}", token=zhangsan, body={"quantity": "5"}
        )
        check("明细接口不收无关字段（不静默忽略）", rejected(status), f"HTTP {status}")

        status, result = call(
            "PATCH", f"/samples/{sid}/items/999999999", token=zhangsan, body={"material": "x"}
        )
        check("改不存在的明细返回 404", status == 404, f"HTTP {status}")

        # === 7.5 驳回不是终态：驳回单改完资料要能重新提交 ===
        # 之前状态机里 rejected 是个空集 —— 驳回过的单子既改不了也重报不了，
        # 只能重开一张，中间的沟通痕迹全断。业务方 2026-10-05 定：驳回可重提。
        print("=== 7.5 驳回可重提：改完资料自动回到「待审批」 ===")
        api("POST", f"/samples/{fresh_id}/approve",
            body={"approved": False, "reject_reason": "费用超预算"}, token=zhangsan)
        rej = api("GET", f"/samples/{fresh_id}", token=zhangsan)
        check("驳回后状态是「已拒绝」", rej["status"] == "rejected", rej["status"])

        # 改的必须**不只**车间依据：只认车间依据的话，因「费用超预算」被驳回的单子
        # 改完费用还是死结 —— 这恰恰是驳回最常见的理由。
        after = api("PATCH", f"/samples/{fresh_id}", token=zhangsan,
                    body={"remark": f"{MARKER} 已重报费用"})
        check("已驳回的单子改非车间依据字段 → 重新提交待审批",
              after["status"] == "pending", after["status"])
        check("重新提交时旧驳回原因保留（主管要看上一轮为什么被打回）",
              after["reject_reason"] == "费用超预算", after["reject_reason"])

        # 明细侧走的是同一道闸门，也要能重提
        api("POST", f"/samples/{fresh_id}/approve",
            body={"approved": False, "reject_reason": "材质不对"}, token=zhangsan)
        after = api("PATCH", f"/samples/{fresh_id}/items/{fresh_item}", token=zhangsan,
                    body={"material": f"{MARKER} 316不锈钢"})
        check("已驳回的单子改明细依据 → 重新提交待审批",
              after["status"] == "pending", after["status"])

        # 同值重发不能算"改了东西"：否则手抖点两下保存就把单子踢回待审批
        api("POST", f"/samples/{fresh_id}/approve",
            body={"approved": False, "reject_reason": "还是不批"}, token=zhangsan)
        same = api("PATCH", f"/samples/{fresh_id}/items/{fresh_item}", token=zhangsan,
                   body={"material": f"{MARKER} 316不锈钢"})
        check("已驳回的单子同值重发不算改动（仍停在已拒绝）",
              same["status"] == "rejected", same["status"])

        # === 7.6 已驳回的两个出口：跟单原样重提 / 主管改判 ===
        # 7.5 测的是"改完资料自动回待审批"。这里测另外两条（业务方 2026-10-05 定）：
        # 一个字不改地原样再报，以及主管发现驳错了直接批回来。
        print("=== 7.6 已驳回的两个出口：原样重提 / 直接改判 ===")
        before_resubmit = api("GET", f"/samples/{fresh_id}", token=zhangsan)
        check("准备条件：该单此刻是「已拒绝」",
              before_resubmit["status"] == "rejected", before_resubmit["status"])

        after = api("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan)
        check("已驳回可以原样重新提交 → 待审批", after["status"] == "pending", after["status"])
        check("原样重提不动物料：备注没被改掉",
              after["remark"] == f"{MARKER} 已重报费用", after["remark"])
        check("原样重提保留上次驳回原因（主管要知道为什么被打回）",
              after["reject_reason"] == "还是不批", after["reject_reason"])

        # 只有"已驳回"需要重提，别的状态调它应该被拦
        status, result = call("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan)
        check("待审批的单子不需要重提（被拦）", rejected(status), f"HTTP {status} {result}")

        # === 7.7 审批轮次：驳回重提是新的一轮，弱网重试不算新轮次 ===
        # 旧实现的通知/时间线事件键是**固定**的 `sample:approve:{id}` / `sample:resubmit:{id}`，
        # 于是第二轮会撞上第一轮的键被去重吞掉——事后只看得到一轮，
        # "驳回过几次、每轮批的是哪版资料"全丢（第一批返修 §3.2）。
        # 上面这串操作已经产生了几轮，先确认轮次真的在涨、且被拦的重提不虚增。
        # 用 .get 取：旧代码的 detail 里没有这个字段，那样应当**干净地 FAIL 而不是崩**，
        # 修复前后的对比才有意义。本文件的 check 是**条件式**（label, condition, detail）。
        round_now = api("GET", f"/samples/{fresh_id}", token=zhangsan).get("review_round")
        check("此刻有明确的审批轮次（第 4 轮，不是恒为 1）", round_now == 4,
              f"review_round={round_now}")
        check("被拦的那次重提没有虚增轮次",
              api("GET", f"/samples/{fresh_id}", token=zhangsan).get("review_round") == round_now,
              f"review_round={round_now}")
        if round_now is not None:
            api("POST", f"/samples/{fresh_id}/approve",
                body={"approved": False, "reject_reason": "轮次回归"}, token=zhangsan)
            r1 = api("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan,
                     body={"request_key": f"CHK{fresh_id}-re-round"})
            check("带键重提开出一个新轮次", r1.get("review_round") == round_now + 1,
                  f"review_round={r1.get('review_round')}")
            # 同一个键再来一次 = 弱网重试：幂等，不加轮次、也不重复通知
            r2 = api("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan,
                     body={"request_key": f"CHK{fresh_id}-re-round"})
            check("同一个请求键重试是幂等的（轮次不变）",
                  r2.get("review_round") == round_now + 1,
                  f"review_round={r2.get('review_round')}")
            # 换一把键：仍然按原口径被拦——**不是"带了键就放行"**
            status, result = call("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan,
                                  body={"request_key": f"CHK{fresh_id}-re-other"})
            check("换一把键仍被拦（幂等只认同一次提交）", rejected(status),
                  f"HTTP {status} {result}")

        # 主管改判：上一次驳错了，直接批回来，不必让跟单先改点什么再绕一圈
        api("POST", f"/samples/{fresh_id}/approve",
            body={"approved": False, "reject_reason": "误驳了"}, token=zhangsan)
        after = api("POST", f"/samples/{fresh_id}/approve",
                    body={"approved": True}, token=zhangsan)
        check("已驳回时主管可直接改判为批准", after["status"] == "approved", after["status"])
        check("改判批准后驳回原因被清掉（结论已经不是驳回了）",
              after["reject_reason"] is None, after["reject_reason"])

        status, result = call("POST", f"/samples/{fresh_id}/resubmit", token=zhangsan)
        check("已批准的单子不能重提（被拦）", rejected(status), f"HTTP {status} {result}")

        # 已批准的单子加明细同样要重批：新明细带进来的材质/工艺/图纸版本也是车间依据，
        # 之前这里只挡了已寄样/已签收，已批准的单子能随便加，是个漏口
        before_add = api("GET", f"/samples/{fresh_id}", token=zhangsan)
        check("准备条件：该单此刻是「已批准」", before_add["status"] == "approved", before_add["status"])
        after = api("POST", f"/samples/{fresh_id}/items", token=zhangsan,
                    body={"sku_id": sku_id, "quantity": "2", "material": f"{MARKER} 追加料"})
        check("已批准的单子加明细 → 退回待审批", after["status"] == "pending", after["status"])

        # ================================================================
        print("=== 8. 打样附件锁 + 制作依据（2026-10-06 补）===")
        # 审视原话：「一张已经制作完成的打样单，仍可以通过通用接口解除附件关联」。
        # 根因：链路上只有 visible_object 一道判断——它答的是「你能不能看见这张
        # 打样单」，与「这张单是否已经制作/寄出」无关；原件保护又只覆盖
        # signed/generated 两类，图纸不在其中。于是通用解绑/删除都能穿过去。
        print("---- 8.1 制作依据要能证明用的是哪份文件（记 sha256，不只是文件名）----")
        lock = api("POST", "/samples", body={
            "customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "3"}]},
            token=zhangsan)
        lock_id = lock["id"]
        sample_ids.append(lock_id)
        # ① 制作**之前**附件可以自由增删：这道锁只在有制作事实之后才生效，
        #    否则单子还在改资料阶段就被锁死，跟单没法干活。
        status, res = upload(zhangsan, "dwg-v1.png", business_type="sample",
                             business_id=lock_id, category="drawing")
        check("制作前传图纸成功", status == 200, f"HTTP {status} {res}")
        dwg_id = res["data"]["id"]
        file_ids.append(dwg_id)
        # 关联行的编号要从清单接口取：上传返回的是文件本身，不含关联编号。
        # 清单行里 `id` 是文件、`business_file_id` 是那条关联（两个别搞混）。
        dwg_link = api("GET", f"/business/sample/{lock_id}/files", token=zhangsan)[0]["business_file_id"]
        status, _ = call("DELETE", f"/business-files/{dwg_link}", token=zhangsan)
        check("制作前解绑图纸照常放行（锁还没生效）", status == 200, f"HTTP {status}")
        # 挂回去，后面要拿它当制作依据
        status, res = call(
            "POST", f"/business/sample/{lock_id}/files?file_id={dwg_id}&category=drawing",
            token=zhangsan,
        )
        check("重新把图纸挂回打样单", status == 200, f"HTTP {status} {res}")

        api("POST", f"/samples/{lock_id}/approve", body={"approved": True}, token=zhangsan)
        made = api("POST", f"/samples/{lock_id}/made", token=zhangsan,
                   body={"remark": "按图纸制作完成", "basis_file_ids": [dwg_id]})
        basis = made.get("basis_files") or []
        check("制作依据记下来了", len(basis) == 1, basis)
        check("依据里带 sha256（光有文件名证明不了是哪一份）",
              bool(basis) and bool(basis[0].get("checksum")), basis[:1])
        check("依据里记了它属于哪一版（V1 的依据不能背书 V2）",
              bool(basis) and basis[0].get("sample_version") == 1, basis[:1])
        check("依据里带原始文件名",
              bool(basis) and basis[0].get("file_name") == "dwg-v1.png", basis[:1])

        print("---- 8.2 已制作之后，三条写入路径都要挡住 ----")
        # ② 解绑：拆掉关联后文件不再挂任何对象，`can_access_file` 对无关联文件
        #    只认上传者——其他人（含主管）从此拿不到那份图纸，账就报不清了。
        links = api("GET", f"/business/sample/{lock_id}/files", token=zhangsan)
        dwg_link = next((r["business_file_id"] for r in links if r["id"] == dwg_id), None)
        status, result = call("DELETE", f"/business-files/{dwg_link}", token=zhangsan)
        check("已制作后解绑图纸被拒", status == 422, f"HTTP {status} {result}")
        # ③ 删除：整份文件清掉等于把凭证毁了
        status, result = call("DELETE", f"/files/{dwg_id}", token=zhangsan)
        check("已制作后删除图纸文件被拒", status == 422, f"HTTP {status} {result}")
        # ④ 挂载：不能再往里塞依据性质的资料（事后补的必须和依据分得开）
        status, result = call(
            "POST", f"/business/sample/{lock_id}/files?file_id={dwg_id}&category=drawing",
            token=zhangsan,
        )
        check("已制作后再挂依据类附件被拒", status == 422, f"HTTP {status} {result}")
        # 但「后续补充资料」必须放行——否则验收报告、整改说明这类正常补料也进不来，
        # 大家只能把资料塞进备注文字里，那才是真的查不到。
        status, res = upload(zhangsan, "验收报告.png", business_type="sample",
                             business_id=lock_id, category="supplement")
        check("锁定后「后续补充资料」仍可上传", status == 200, f"HTTP {status} {res}")
        extra_id = res["data"]["id"]
        file_ids.append(extra_id)
        after = api("GET", f"/samples/{lock_id}", token=zhangsan)
        check("补充资料没有被算成制作依据",
              len(after.get("basis_files") or []) == 1, after.get("basis_files"))

        print("---- 8.3 依据登记即固化 ----")
        status, result = call(
            "POST", f"/samples/{lock_id}/made", token=zhangsan,
            body={"remark": "换一份图纸", "basis_file_ids": [extra_id]},
        )
        check("想改已登记的制作依据被拒（要开修订版）", status == 422, f"HTTP {status} {result}")

        print("---- 8.4 依据必须挂在这一单上，不能拿别处的文件背书 ----")
        stranger = api("POST", "/samples", body={
            "customer_id": own.id, "items": [{"sku_id": sku_id, "quantity": "1"}]},
            token=zhangsan)
        stranger_id = stranger["id"]
        sample_ids.append(stranger_id)
        api("POST", f"/samples/{stranger_id}/approve", body={"approved": True}, token=zhangsan)
        status, result = call(
            "POST", f"/samples/{stranger_id}/made", token=zhangsan,
            body={"remark": "拿别单的图纸当依据", "basis_file_ids": [dwg_id]},
        )
        check("拿别张打样单的图纸当依据被拒", status == 422, f"HTTP {status} {result}")

        print("---- 8.5 「附件锁没锁」要由后端下发（前端附件区靠它）----")
        # 2026-10-08 补打样附件区时加的。前端附件区要据此决定两件事：
        # ① 要不要提示"现在传的会自动归到后续补充资料"；
        # ② 上传时要不要**自动**带上 `category=supplement`。
        # 判据在后端（`file/access.sample_write_lock_label_for`）；前端**不许**自己按
        # made_at / status 推一遍 —— 推出来的那份迟早和后端分叉，而分叉的后果是
        # 上传被 422 挡住、用户还不知道为什么。下面两条就是钉住"它真的下发了"。
        locked_detail = api("GET", f"/samples/{lock_id}", token=zhangsan)
        check("已制作的单子：详情说附件已锁定",
              locked_detail.get("attachment_locked") is True,
              locked_detail.get("attachment_lock"))
        check("锁定原因说了是哪一步锁的（人话，直接能显示给用户）",
              bool(locked_detail.get("attachment_lock")), locked_detail.get("attachment_lock"))
        open_detail = api("GET", f"/samples/{stranger_id}", token=zhangsan)
        check("还没制作的单子：详情说没锁",
              open_detail.get("attachment_locked") is False, open_detail.get("attachment_lock"))
        # 详情优先复用**列表**里那条对象（省一次请求），所以列表也得带 ——
        # 只给详情不带列表的话，从列表点进来的详情会显示成"没锁"。
        listed = api("GET", "/samples?page_size=200", token=zhangsan)
        listed_row = next((r for r in (listed.get("items") or []) if r["id"] == lock_id), None)
        # ⚠️ 本套件的 `check` 是"给条件"那一版（不是 `check(实际, 期望)`），别串。
        check("列表里也带这两个字段（详情会复用它）",
              listed_row is not None and "attachment_locked" in listed_row,
              listed_row.get("attachment_locked") if listed_row else "没找到这条")

        # 界面忘了自动带类别时，用户会撞在这个 422 上 —— 把它钉住，
        # 免得以后有人把闸门放松成"锁定后随便传"。
        status, result = upload(
            zhangsan, "忘了带类别.png", business_type="sample", business_id=lock_id
        )
        check("锁定后**不带类别**上传被拒（界面必须自动带「后续补充资料」）",
              status == 422, f"HTTP {status} {result}")

    finally:
        async with SessionLocal() as session:
            if order_ids:
                # 自底向上清：批次明细 → 批次 → 里程碑/状态流转/交期变更 → 订单明细 → 订单
                await session.execute(
                    delete(OrderShipmentBatchItem).where(
                        OrderShipmentBatchItem.batch_id.in_(
                            select(OrderShipmentBatch.id).where(OrderShipmentBatch.order_id.in_(order_ids))
                        )
                    )
                )
                await session.execute(delete(OrderShipmentBatch).where(OrderShipmentBatch.order_id.in_(order_ids)))
                await session.execute(delete(OrderMilestone).where(OrderMilestone.order_id.in_(order_ids)))
                await session.execute(delete(OrderStatusHistory).where(OrderStatusHistory.order_id.in_(order_ids)))
                await session.execute(delete(OrderScheduleChange).where(OrderScheduleChange.order_id.in_(order_ids)))
                await session.execute(delete(SalesOrderItem).where(SalesOrderItem.order_id.in_(order_ids)))
                await session.execute(delete(SalesOrder).where(SalesOrder.id.in_(order_ids)))
            if sample_ids:
                await session.execute(delete(SampleShipment).where(SampleShipment.sample_request_id.in_(sample_ids)))
                await session.execute(delete(SampleItem).where(SampleItem.sample_request_id.in_(sample_ids)))
                await session.execute(delete(SampleRequest).where(SampleRequest.id.in_(sample_ids)))
            if file_ids:
                keys = (await session.execute(select(FileRecord.object_key).where(FileRecord.id.in_(file_ids)))).scalars().all()
                await session.execute(delete(BusinessFile).where(BusinessFile.file_id.in_(file_ids)))
                await session.execute(delete(FileRecord).where(FileRecord.id.in_(file_ids)))
                for key in keys:
                    try:
                        storage.delete_object(key)
                    except Exception:  # noqa: BLE001
                        pass
            if doc_ids:
                await session.execute(delete(ContractDocument).where(ContractDocument.id.in_(doc_ids)))
            if template_ids:
                await session.execute(delete(ContractTemplate).where(ContractTemplate.id.in_(template_ids)))
            if quote_ids:
                # 报价版本先删（外键指向 quotes），再删报价单；都在删客户之前
                from app.modules.quote.model import Quote as _Quote
                from app.modules.quote.model import QuoteVersion as _QuoteVersion

                await session.execute(
                    delete(_QuoteVersion).where(_QuoteVersion.quote_id.in_(quote_ids))
                )
                await session.execute(delete(_Quote).where(_Quote.id.in_(quote_ids)))
            if customer_ids:
                await session.execute(delete(FollowUp).where(FollowUp.customer_id.in_(customer_ids)))
                await session.execute(delete(BusinessEvent).where(BusinessEvent.customer_id.in_(customer_ids)))
                await session.execute(delete(Notification).where(Notification.content.contains(MARKER)))
                await session.execute(delete(CustomInquiry).where(CustomInquiry.customer_id.in_(customer_ids)))
                await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            await session.execute(delete(AuditLog).where(AuditLog.after_data.cast(__import__("sqlalchemy").String).contains(MARKER)))
            await session.commit()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print("\nOK 附件解绑与可见性、询价附件类型、删除保护、打样入参、重试去重、批次节点一致性、车间依据闸门、驳回重提与改判、打样附件锁与制作依据")


if __name__ == "__main__":
    asyncio.run(main())
