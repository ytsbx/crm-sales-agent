"""第三轮审查 7 项修复的真实验证（必须在一次性 PostgreSQL 库上跑）。

跑法（仓库根；库名必须是 crm_iso / crm_check 开头的一次性库）：

    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE crm_check_x OWNER crm;"
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_x PYTHONPATH=. .venv/bin/alembic upgrade head
    DATABASE_URL=... PYTHONPATH=. .venv/bin/python scripts/seed.py
    API_BASE=http://127.0.0.1:8079/api/v1 DATABASE_URL=... FILE_ROOT=data/iso-files-x \
      PYTHONPATH=. .venv/bin/python scripts/check_round3_risk_repairs.py

探针（对应第三轮 7 项）：
1. 附件预览的类型判定：带参数的 `text/html; charset=utf-8`、类型与后缀互相矛盾、
   svg/xml 全部拒绝；合法图片/PDF/文本仍可预览，且响应头用**服务端认可**的类型；
2. 跟单「收款」节点按财务事实重建：应收改大后撤销完成、补录更早回款重算定金、
   先确认回款后首次打开跟单页也能补上；
3. 实际发货日不得晚于业务今天，且未来凭据不能用于"已完成"；
4. 客户进公海（负责人为空）后，合同详情与签署附件**同判据**：都能看/都能下；
5. 商机复制与订单复购带入停用/删除 SKU 时整次拒绝，不留新商机与部分明细；
6. 被续签替代的旧月结协议不再产生新的到期提醒（含"提前替代、当时没有待办"的顺序）；
7. 月结续签缺生效日被拒（且失败发生在生成、取号、取消旧待办之前），生效日不得晚于到期日。

跑完自己清掉本轮夹具，不留脏数据。
"""

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select, text  # noqa: E402

from _test_support import require_api_base, require_isolated_db  # noqa: E402

require_api_base()
require_isolated_db()

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.core.timebase import today_business  # noqa: E402
from app.modules.contract import service as contract_service  # noqa: E402
from app.modules.contract import visibility as contract_visibility  # noqa: E402
from app.modules.contract.model import ContractDocument  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.file import access as file_access  # noqa: E402
from app.modules.file.model import FileRecord  # noqa: E402
from app.modules.file.router import is_previewable, preview_media_type  # noqa: E402
from app.modules.opportunity import service as opportunity_service  # noqa: E402
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStage  # noqa: E402
from app.modules.order import milestones as milestones_svc  # noqa: E402
from app.modules.order import service as order_service  # noqa: E402
from app.modules.order.model import OrderMilestone, OrderShipmentBatch, SalesOrder, SalesOrderItem  # noqa: E402
from app.modules.payment import service as payment_service  # noqa: E402
from app.modules.payment.model import PaymentRecord, ReceivablePlan  # noqa: E402
from app.modules.product.model import Product, Sku  # noqa: E402
from app.modules.task.model import Task  # noqa: E402
from app.modules.user.model import User  # noqa: E402

PREFIX = f"CHK3{int(time.time())}"
FAILURES: list[str] = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def code_of(error, default=None):
    return getattr(error, "code", default) if error is not None else None


@asynccontextmanager
async def sandbox():
    """夹具放在可回滚的外层事务里：探针内部提交也不留脏数据。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with engine.connect() as connection:
        outer = await connection.begin()
        try:
            async with AsyncSession(
                bind=connection, expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                yield session
        finally:
            if outer.is_active:
                await outer.rollback()


# --------------------------------------------------------------------- 夹具


async def make_customer(session, *, owner_id: int | None, name_suffix: str = ""):
    tag = f"{PREFIX}{uuid4().hex[:8]}{name_suffix}"
    customer = Customer(name=tag, owner_id=owner_id, status="active", level="A")
    session.add(customer)
    await session.flush()
    return customer


async def make_sku(session, *, sku_status: str = "active", product_status: str = "active"):
    tag = f"{PREFIX}{uuid4().hex[:8]}"
    product = Product(name=tag, status=product_status)
    session.add(product)
    await session.flush()
    sku = Sku(product_id=product.id, sku_code=tag, name=tag, unit="件",
              specification="标准", status=sku_status)
    session.add(sku)
    await session.flush()
    return sku


async def make_order(
    session, *, owner_id: int | None, customer: Customer | None = None,
    status: str = "pending",
) -> SalesOrder:
    customer = customer or await make_customer(session, owner_id=owner_id)
    order = SalesOrder(
        order_no=f"{PREFIX}-SO-{uuid4().hex[:8]}",
        customer_id=customer.id, owner_id=owner_id, status=status,
        currency="CNY", total_amount=Decimal("100"), created_at=datetime.now(UTC),
    )
    session.add(order)
    await session.flush()
    return order


# --------------------------------------------------------------------- 探针


async def probe_preview_types():
    """① 附件预览的类型判定（纯函数级 + 真实记录的序列化标记）。"""
    print("=== 1. 附件预览的类型判定 ===")
    cases = [
        # (mime_type, file_name, 期望能预览?, 期望下发的类型)
        ("text/html; charset=utf-8", "drawing.png", False, None),
        ("text/html", "drawing.png", False, None),
        ("TEXT/HTML;charset=UTF-8", "drawing.png", False, None),
        ("image/svg+xml", "logo.png", False, None),
        ("image/png", "logo.svg", False, None),
        ("image/png", "photo.png", True, "image/png"),
        ("image/png; charset=binary", "photo.png", True, "image/png"),
        ("application/pdf", "合同.pdf", True, "application/pdf"),
        ("text/plain", "说明.txt", True, "text/plain"),
        ("application/octet-stream", "未知.txt", True, "text/plain"),
        ("", "无名.png", True, "image/png"),
        ("application/x-msdownload", "payload.png", False, None),
        ("text/xml", "清单.xml", False, None),
    ]
    for mime, name, previewable, safe_type in cases:
        record = FileRecord(file_name=name, mime_type=mime or None, object_key="k", size=1,
                            checksum="x")
        got = preview_media_type(record)
        check_true(
            f"{mime or '（空）'} + {name} → {'可预览' if previewable else '拒绝'}",
            is_previewable(record) is previewable and got == safe_type,
            f"previewable={is_previewable(record)}, type={got!r}",
        )


async def probe_milestones(session, owner_id: int, user):
    """② 跟单「收款」「付定金」按财务事实重建。"""
    print("=== 2. 跟单节点按财务事实重建 ===")
    order = await make_order(session, owner_id=owner_id)
    plan = ReceivablePlan(
        order_id=order.id, plan_name="全款", due_date=date(2026, 11, 1),
        amount=Decimal("100"), currency="CNY", status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    await session.flush()
    session.add(PaymentRecord(
        order_id=order.id, receivable_plan_id=plan.id, received_date=date(2026, 10, 1),
        received_amount=Decimal("100"), currency="CNY", status="confirmed",
        confirmed_at=datetime.now(UTC), created_at=datetime.now(UTC),
    ))
    await session.flush()
    await payment_service.recalc_plan(session, plan)
    await session.flush()

    # 先模拟"打开过跟单页"（节点已建），这样下面遇到的是审查报告的**真实症状**：
    # 节点先建好、随后财务事实变了，旧实现只补不撤 → 节点永远停在"已完成"。
    await milestones_svc.ensure_initialized(session, order.id, None, created_by=user.id)
    await session.flush()
    # 应收 100、已确认 100 → 收齐
    await milestones_svc.sync_payment_milestones(session, order.id)
    nodes = {row.node: row for row in (await session.execute(
        select(OrderMilestone).where(OrderMilestone.order_id == order.id)
    )).scalars().all()}
    check_true("节点已按需补建", {"deposit", "payment"} <= set(nodes))
    if "payment" not in nodes or "deposit" not in nodes:
        # 旧实现下这里会缺节点：明确记失败并停下（后续断言依赖这两个节点）
        check("收齐后「收款」实际日", None, date(2026, 10, 1))
        check("「付定金」实际日", None, date(2026, 10, 1))
        return
    check("收齐后「收款」实际日", nodes["payment"].actual_date, date(2026, 10, 1))
    check("「付定金」实际日", nodes["deposit"].actual_date, date(2026, 10, 1))

    # 把应收改成 200：应收状态变 partial，收款节点必须**退回未完成**
    plan.amount = Decimal("200")
    await payment_service.recalc_plan(session, plan)
    await session.flush()
    await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await session.flush()
    check("应收改大后应收状态", plan.status, "partial")
    check("应收改大后「收款」节点实际日被撤销", nodes["payment"].actual_date, None)

    revoked = (await session.execute(
        text(
            "select count(*) from audit_logs where action = 'milestone_revoked' "
            "and business_id = :i"
        ),
        {"i": nodes["payment"].id},
    )).scalar_one()
    check_true("撤销留了纠正痕迹", int(revoked) >= 1, f"{revoked} 条")

    # 补录一笔更早的到账 → 定金日重算为最早那笔
    early_plan = ReceivablePlan(
        order_id=order.id, plan_name="定金", due_date=date(2026, 9, 1),
        amount=Decimal("50"), currency="CNY", status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(early_plan)
    await session.flush()
    early_receipt = PaymentRecord(
        order_id=order.id, receivable_plan_id=early_plan.id, received_date=date(2026, 9, 5),
        received_amount=Decimal("1000"), currency="CNY", status="confirmed",
        confirmed_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    session.add(early_receipt)
    await session.flush()
    # 确认入口明确分配；同步节点只读，不再懒改回款归属。
    await payment_service.allocate_payment(session, early_receipt, operator_id=user.id)
    await milestones_svc.sync_payment_milestones(session, order.id)
    await session.flush()
    check("补录更早回款后定金日重算", nodes["deposit"].actual_date, date(2026, 9, 5))
    check("多收之后「收款」节点重新完成", nodes["payment"].actual_date, date(2026, 10, 1))

    # 初始化遗漏：先有确认回款、后首次打开跟单页（节点此刻才建）
    order2 = await make_order(session, owner_id=owner_id)
    plan2 = ReceivablePlan(
        order_id=order2.id, plan_name="全款", due_date=date(2026, 11, 1),
        amount=Decimal("100"), currency="CNY", status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(plan2)
    await session.flush()
    session.add(PaymentRecord(
        order_id=order2.id, receivable_plan_id=plan2.id, received_date=date(2026, 10, 2),
        received_amount=Decimal("100"), currency="CNY", status="confirmed",
        confirmed_at=datetime.now(UTC), created_at=datetime.now(UTC),
    ))
    await session.flush()
    before = (await session.execute(
        select(OrderMilestone).where(OrderMilestone.order_id == order2.id)
    )).scalars().all()
    check("回款确认时还没有节点", len(before), 0)
    await milestones_svc.sync_payment_milestones(session, order2.id)
    await session.flush()
    nodes2 = {row.node: row for row in (await session.execute(
        select(OrderMilestone).where(OrderMilestone.order_id == order2.id)
    )).scalars().all()}
    check_true("首次同步就把两个节点补上", {"deposit", "payment"} <= set(nodes2))
    if "payment" not in nodes2 or "deposit" not in nodes2:
        check("补建后「付定金」实际日", None, date(2026, 10, 2))
        check("补建后「收款」实际日", None, date(2026, 10, 2))
        return
    check("补建后「付定金」实际日", nodes2["deposit"].actual_date, date(2026, 10, 2))
    check("补建后「收款」实际日", nodes2["payment"].actual_date, date(2026, 10, 2))


async def probe_ship_date(session, owner_id: int, user):
    """③ 实际发货日不得晚于业务今天；未来凭据不能用于完成。"""
    print("=== 3. 未来发货日被拒 ===")
    order = await make_order(session, owner_id=owner_id)
    sku = await make_sku(session)
    session.add(SalesOrderItem(
        order_id=order.id, sku_id=sku.id, sku_snapshot=sku.sku_code,
        quantity=Decimal("10"), unit_price=Decimal("10"), amount=Decimal("100"),
    ))
    await session.flush()
    batch = OrderShipmentBatch(
        order_id=order.id, batch_no=1, status="planned",
        planned_date=today_business(), created_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    item = (await session.execute(
        select(SalesOrderItem).where(SalesOrderItem.order_id == order.id)
    )).scalars().first()
    from app.modules.order.model import OrderShipmentBatchItem

    batch_item = OrderShipmentBatchItem(
        batch_id=batch.id, order_item_id=item.id, planned_qty=Decimal("10"),
        shipped_qty=Decimal("10"),
    )
    session.add(batch_item)
    await session.flush()

    class _Payload:
        actual_ship_date = today_business() + timedelta(days=10)
        logistics_company = None
        tracking_no = None
        overdue_reason = None
        remark = None
        items = None

    before_shipped = batch_item.shipped_qty
    try:
        await order_service.ship_shipment_batch(
            session, order=order, batch=batch, payload=_Payload(), operator_id=user.id
        )
        error = None
    except AppError as exc:
        error = exc
    check_true("未来发货日被拒", error is not None, str(error))
    check("拒绝码是参数类", code_of(error), ErrorCode.PARAM_ERROR)
    await session.refresh(batch)
    check("被拒后批次状态没变", batch.status, "planned")
    check("被拒后没有写实际发货日", batch.actual_ship_date, None)
    await session.refresh(batch_item)
    check("被拒后本批实发数量没被改动", batch_item.shipped_qty, before_shipped)

    # 历史脏数据（库里已是未来日期）也不能作为"完成"的依据
    batch.status = "shipped"
    batch.actual_ship_date = today_business() + timedelta(days=3)
    batch_item.shipped_qty = Decimal("10")
    order.status = "shipped"
    await session.flush()
    try:
        await order_service.change_status(
            session, order, new_status="completed", operator_id=user.id
        )
        error2 = None
    except AppError as exc:
        error2 = exc
    check_true("未来凭据不能标完成", error2 is not None, str(error2))
    check("完成闸门码", code_of(error2), ErrorCode.STATUS_NOT_ALLOWED)


async def probe_contract_visibility(session, owner_id: int, user, other_user, staff_ids):
    """④ 公海客户的合同：详情与附件同判据。

    ⚠️ 调用者必须是**数据范围受限**的人（`scope=self` 的订单负责人）：
    管理员是"看全部"，两种口径都放行，拿管理员验这条等于没验。
    """
    print("=== 4. 公海客户的合同附件 ===")
    # 客户负责人为空（进了公海），订单负责人是本次的受限用户
    from app.modules.user.model import Role, User, user_roles

    tag = f"{PREFIX}{uuid4().hex[:6]}"
    role = Role(code=tag, name=f"{PREFIX}受限角色", data_scope="self")
    session.add(role)
    await session.flush()
    scoped = User(
        name=f"{PREFIX}订单负责人", username=f"{PREFIX.lower()}_{tag}_scoped".lower(),
        password_hash="x", status="active",
    )
    session.add(scoped)
    await session.flush()
    await session.execute(user_roles.insert().values(user_id=scoped.id, role_id=role.id))
    await session.flush()
    # 权限码要**逐个列**（`_permitted` 是"集合里有没有这个码"，不是通配符）：
    # 给 {"*"} 会被判成没有合同/文件查看权，附件那条根本走不到归属判据
    scoped_user = CurrentUser(
        scoped, {"contract:view", "file:view", "order:view"}, [role.code], "self"
    )
    staff_ids.append(scoped.id)

    customer = await make_customer(session, owner_id=None)
    order = await make_order(session, owner_id=scoped.id, customer=customer)
    template_id = await _any_template_id(session)
    doc = ContractDocument(
        doc_no=f"{PREFIX}-C-{uuid4().hex[:6]}", doc_type="contract",
        title="公海客户合同", customer_id=customer.id, order_id=order.id,
        template_id=template_id, content_snapshot="检查用正文",
        status="signed", created_by=other_user.id, created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    check("归属判据回退到订单负责人",
          await contract_visibility.contract_scope_owner_id(session, doc), scoped.id)
    check_true("合同详情可见性通过（回退到订单负责人）",
               await contract_visibility.contract_in_scope(session, scoped_user, doc))
    record = FileRecord(
        file_name=f"{PREFIX}-签署件.pdf", mime_type="application/pdf",
        object_key=f"test/{PREFIX}.pdf", size=1, checksum="x",
    )
    session.add(record)
    await session.flush()
    from app.modules.file.model import BusinessFile

    session.add(BusinessFile(
        business_type="contract", business_id=doc.id, file_id=record.id,
        category="signed",
    ))
    await session.flush()
    allowed = await file_access.can_access_file(session, scoped_user, record.id)
    check_true("同一份合同的附件也能访问（同判据）", allowed)

    # 客户没负责人、订单也没负责人时，回退到创建人（创建人自己能看到）
    order2 = await make_order(session, owner_id=None, customer=customer)
    doc2 = ContractDocument(
        doc_no=f"{PREFIX}-C2-{uuid4().hex[:6]}", doc_type="contract",
        title="无订单负责人合同", customer_id=customer.id, order_id=order2.id,
        template_id=template_id, content_snapshot="检查用正文",
        status="signed", created_by=scoped.id, created_at=datetime.now(UTC),
    )
    session.add(doc2)
    await session.flush()
    check("无订单负责人时回退到创建人",
          await contract_visibility.contract_scope_owner_id(session, doc2), scoped.id)
    check_true("创建人自己能打开",
               await contract_visibility.contract_in_scope(session, scoped_user, doc2))


async def probe_copy_sku_guard(session, owner_id: int, user):
    """⑤ 复制商机 / 订单复购不得带入停用或删除的 SKU。"""
    print("=== 5. 复制与复购的 SKU 可用性 ===")
    stage = (await session.execute(select(OpportunityStage).limit(1))).scalar_one()
    good = await make_sku(session)
    disabled = await make_sku(session, sku_status="disabled")
    deleted = await make_sku(session, product_status="disabled")
    customer = await make_customer(session, owner_id=owner_id)

    source = Opportunity(
        customer_id=customer.id, title=f"{PREFIX}-源商机", stage_id=stage.id,
        owner_id=owner_id, status="open", created_by=user.id,
    )
    session.add(source)
    await session.flush()
    session.add(OpportunityItem(
        opportunity_id=source.id, sku_id=good.id, quantity=Decimal("1"),
        currency="CNY",
    ))
    session.add(OpportunityItem(
        opportunity_id=source.id, sku_id=disabled.id, quantity=Decimal("2"),
        currency="CNY",
    ))
    await session.flush()

    count_before = (await session.execute(
        select(Opportunity.id).where(Opportunity.title.like(f"{PREFIX}%（复制）%"))
    )).scalars().all()
    try:
        await opportunity_service.clone_opportunity(session, source=source, user=user)
        error = None
    except AppError as exc:
        error = exc
    check_true("复制商机被整次拒绝", error is not None, str(error))
    check("拒绝码", code_of(error), ErrorCode.STATUS_NOT_ALLOWED)
    check_true("错误里点名了不可用 SKU",
               disabled.sku_code in (getattr(error, "message", "") or ""),
               (getattr(error, "message", "") or "")[:120])
    await session.flush()
    count_after = (await session.execute(
        select(Opportunity.id).where(Opportunity.title.like(f"{PREFIX}%（复制）%"))
    )).scalars().all()
    check("没有留下新商机", len(count_after), len(count_before))

    # 复购：老订单里有停用 SKU
    order = await make_order(session, owner_id=owner_id)
    session.add(SalesOrderItem(
        order_id=order.id, sku_id=disabled.id, sku_snapshot=disabled.sku_code,
        quantity=Decimal("1"), unit_price=Decimal("10"), amount=Decimal("10"),
    ))
    await session.flush()
    from app.modules.product.service import ensure_skus_for_new_business

    try:
        await ensure_skus_for_new_business(session, [disabled.id])
        error2 = None
    except AppError as exc:
        error2 = exc
    check_true("复购的判据同样拒绝停用 SKU", error2 is not None, str(error2))
    try:
        await ensure_skus_for_new_business(session, [deleted.id])
        error3 = None
    except AppError as exc:
        error3 = exc
    check_true("产品被停用的 SKU 也被拒", error3 is not None, str(error3))
    # 正常 SKU 必须放行（不能误伤）
    await ensure_skus_for_new_business(session, [good.id])
    check_true("可用 SKU 正常放行", True, good.sku_code)


async def probe_superseded_reminder(session, owner_id: int, user):
    """⑥ 被续签替代的旧协议不再产生新提醒（含"提前替代、当时没有待办"的顺序）。

    顺序必须照审查给的那样走，否则验不出问题：
      ① 旧协议**还在提醒窗口之外**时被替代 → 那一刻没有任何待办可结束；
      ② 之后时间推进到窗口内（这里用改到期日模拟）→ 再扫一次。
    旧写法会在第 ② 步凭空建出一条提醒；新写法必须跳过。
    """
    print("=== 6. 被替代的月结协议不再提醒 ===")
    from app.modules.settings import service as settings_service

    days = int(await settings_service.get_number(session, "contract", "monthly_remind_days", 30))
    customer = await make_customer(session, owner_id=owner_id)
    template_id = await _any_template_id(session)

    # 阶段①：到期日远在窗口之外
    old = ContractDocument(
        doc_no=f"{PREFIX}-M-{uuid4().hex[:6]}", doc_type="monthly",
        title="月结协议（旧）", customer_id=customer.id, template_id=template_id,
        content_snapshot="检查用正文", status="signed",
        expiry_date=today_business() + timedelta(days=days + 60),
        effective_date=today_business() - timedelta(days=100),
        created_by=user.id, created_at=datetime.now(UTC),
    )
    fresh = ContractDocument(
        doc_no=f"{PREFIX}-M2-{uuid4().hex[:6]}", doc_type="monthly",
        title="月结协议（新）", customer_id=customer.id, template_id=template_id,
        content_snapshot="检查用正文", status="signed",
        expiry_date=today_business() + timedelta(days=5),
        effective_date=today_business(),
        created_by=user.id, created_at=datetime.now(UTC),
    )
    session.add_all([old, fresh])
    await session.flush()
    # 窗口外先扫一次：一份提醒都不该有（这一步两版行为一致，作为对照）
    await contract_service.notify_expiring_monthly(session, owner_id=owner_id)
    await session.flush()
    early = (await session.execute(
        select(Task).where(Task.source_business_id == old.id)
    )).scalars().all()
    check("窗口外时不产生提醒（替代就发生在这时候）", len(early), 0)

    # 续签替代（只写标记；此刻没有待办可结束 —— 这正是漏洞的前提）
    old.filled_data = {**(old.filled_data or {}), "_superseded_by": {"id": fresh.id, "doc_no": fresh.doc_no}}
    await session.flush()
    check_true("替代标记被识别", contract_service.is_superseded(old))
    check_true("被替代的协议不应再提醒", not contract_service.should_remind(old))
    check_true("未被替代的协议仍应提醒", contract_service.should_remind(fresh))

    # 阶段②：时间推进到窗口内（改到期日模拟）后重扫
    old.expiry_date = today_business() + timedelta(days=3)
    await session.flush()
    created = await contract_service.notify_expiring_monthly(session, owner_id=owner_id)
    await session.flush()
    tasks = (await session.execute(
        select(Task).where(Task.source_business_id.in_([old.id, fresh.id]))
    )).scalars().all()
    by_doc = {}
    for task in tasks:
        by_doc.setdefault(task.source_business_id, []).append(task)
    check_true("新协议建了提醒", fresh.id in by_doc, f"created={created}")
    check_true("进入窗口后，被替代的旧协议**没有**建提醒", old.id not in by_doc,
               f"tasks={[(t.source_business_id, t.status) for t in tasks]}")


async def probe_renewal_effective_date(session, owner_id: int, user, doc_id_holder):
    """⑦ 月结续签必须填生效日，且失败发生在生成／取号／取消旧待办之前。"""
    print("=== 7. 月结续签的生效日必填 ===")
    customer = await make_customer(session, owner_id=owner_id)
    parent = ContractDocument(
        doc_no=f"{PREFIX}-P-{uuid4().hex[:6]}", doc_type="monthly",
        title="待续签月结", customer_id=customer.id,
        template_id=await _any_template_id(session),
        content_snapshot="检查用正文",
        status="signed",
        expiry_date=today_business() + timedelta(days=10),
        effective_date=today_business() - timedelta(days=60),
        created_by=user.id, created_at=datetime.now(UTC),
    )
    session.add(parent)
    await session.flush()
    session.add(Task(
        title=f"月结协议 {parent.doc_no} 将于 {parent.expiry_date} 到期",
        task_type="followup", customer_id=customer.id, owner_id=owner_id,
        priority="high", status="pending",
        due_at=datetime.combine(parent.expiry_date, datetime.min.time(), tzinfo=UTC),
        source="system", source_business_type="contract", source_business_id=parent.id,
        source_key=f"{PREFIX}-task-{parent.id}",
    ))
    await session.flush()
    from app.modules.contract.schema import DocumentCreate

    number_before = (await session.execute(
        text("select coalesce(sum(current_no), 0) from number_sequences")
    )).scalar_one()

    payload = DocumentCreate(
        template_id=(await _monthly_template_id(session)),
        customer_id=customer.id,
        parent_id=parent.id,
        supersede_parent=True,
        expiry_date=today_business() + timedelta(days=375),
        effective_date=None,
    )
    try:
        await contract_service.generate_document(session, payload=payload, user_id=user.id)
        error = None
    except AppError as exc:
        error = exc
    check_true("缺生效日被拒", error is not None, str(error))
    check("拒绝码=必填缺失", code_of(error), ErrorCode.REQUIRED_FIELD_MISSING)

    number_after = (await session.execute(
        text("select coalesce(sum(current_no), 0) from number_sequences")
    )).scalar_one()
    check("失败发生在**取号之前**", int(number_after), int(number_before))

    docs = (await session.execute(
        select(ContractDocument).where(ContractDocument.parent_id == parent.id)
    )).scalars().all()
    check("没有生成新文档", len(docs), 0)
    task = (await session.execute(
        select(Task).where(Task.source_business_id == parent.id)
    )).scalars().first()
    check("旧协议的待办没有被取消", task.status, "pending")
    check("旧协议没有被打上替代标记",
          contract_service.is_superseded(
              (await session.execute(
                  select(ContractDocument).where(ContractDocument.id == parent.id)
              )).scalar_one()
          ), False)

    # 生效日晚于到期日 → 拒绝
    payload2 = DocumentCreate(
        template_id=(await _monthly_template_id(session)),
        customer_id=customer.id,
        parent_id=parent.id,
        expiry_date=today_business() + timedelta(days=30),
        effective_date=today_business() + timedelta(days=60),
    )
    try:
        await contract_service.generate_document(session, payload=payload2, user_id=user.id)
        error2 = None
    except AppError as exc:
        error2 = exc
    check_true("生效日晚于到期日被拒", error2 is not None, str(error2))
    check("拒绝码=参数类", code_of(error2), ErrorCode.PARAM_ERROR)
    _ = doc_id_holder


async def _ensure_template(session, doc_type: str) -> int:
    """夹具用模板：种子数据**不建**合同模板，所以这里按需建一份最小可用的。"""
    from app.modules.contract.model import ContractTemplate

    row = (await session.execute(
        select(ContractTemplate).where(ContractTemplate.doc_type == doc_type)
        .limit(1)
    )).scalars().first()
    if row is not None:
        return row.id
    row = ContractTemplate(
        doc_type=doc_type,
        name=f"检查用{doc_type}模板",
        version=1,
        body="甲方：{{customer.name}}\n签订日期：{{today}}",
        enabled=True,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row.id


async def _any_template_id(session) -> int:
    return await _ensure_template(session, "contract")


async def _monthly_template_id(session) -> int:
    return await _ensure_template(session, "monthly")


async def cleanup():
    async with SessionLocal() as s:
        for sql in (
            "delete from audit_logs where business_type in ('order_milestone','receivable_plan') and business_id in (select id from order_milestones where order_id in (select id from sales_orders where order_no like :p))",
            "delete from tasks where source_business_id in (select id from contract_documents where customer_id in (select id from customers where name like :p))",
            "delete from tasks where title like :p",
            "delete from contract_documents where doc_no like :p",
            "delete from order_milestones where order_id in (select id from sales_orders where order_no like :p)",
            "delete from payment_records where order_id in (select id from sales_orders where order_no like :p)",
            "delete from receivable_plans where order_id in (select id from sales_orders where order_no like :p)",
            "delete from order_status_history where order_id in (select id from sales_orders where order_no like :p)",
            "delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in (select id from sales_orders where order_no like :p))",
            "delete from order_shipment_batches where order_id in (select id from sales_orders where order_no like :p)",
            "delete from sales_order_items where order_id in (select id from sales_orders where order_no like :p)",
            "delete from sales_orders where order_no like :p",
            "delete from opportunity_items where opportunity_id in (select id from opportunities where title like :p)",
            "delete from opportunities where title like :p",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
            "delete from customers where name like :p",
            "delete from users where lower(username) like lower(:p)",
        ):
            try:
                await s.execute(text(sql), {"p": f"{PREFIX}%"})
                await s.commit()
            except Exception as error:  # noqa: BLE001 —— 清理失败不该顶掉结论
                print(f"  （清理告警：{type(error).__name__}: {error}）")
                await s.rollback()
    await engine.dispose()


async def main():
    async with SessionLocal() as s:
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one()
        other = (await s.execute(select(User).where(User.username == "zhangsan"))).scalar_one()
        owner_id = admin.id
        user = CurrentUser(admin, {"*"}, ["admin"], "all")
        other_user = CurrentUser(other, {"*"}, ["admin"], "all")
    print(f"夹具前缀：{PREFIX}")

    staff_ids: list[int] = []
    await probe_preview_types()

    async with sandbox() as session:
        await probe_milestones(session, owner_id, user)
        await probe_ship_date(session, owner_id, user)
        await probe_contract_visibility(session, owner_id, user, other_user, staff_ids)
        await probe_copy_sku_guard(session, owner_id, user)
        await probe_superseded_reminder(session, owner_id, user)
        await probe_renewal_effective_date(session, owner_id, user, None)

    await cleanup()
    print()
    if FAILURES:
        print(f"发现 {len(FAILURES)} 项不符合预期：")
        for name in FAILURES:
            print(f"  - {name}")
    else:
        print("全部探针通过。")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
