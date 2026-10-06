"""客户导入导出接口。"""

import hashlib

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.importing import ImportReport, RowErrors, finalize, row_savepoint
from app.modules.contact_util import find_duplicate_customers
from app.modules.customer import io as io_util
from app.modules.customer import service as svc
from app.modules.customer.model import Customer
from app.modules.customer.schema import CustomerExportFilter, CustomerExportPurpose
from app.modules.user.model import User

router = APIRouter(tags=["Customer"])


@router.get("/customers/import-template")
async def import_template(
    _: CurrentUser = Depends(require_permission("customer:create")),
):
    """下载导入模板：表头 + 一行示例。

    示例行必须与表头**一一对齐**（第七批 7.5）：原来表头 9 列、示例只有 8 个值，
    于是"备注"落进了"最后联系日期"列 —— 照模板填的人会把备注写在日期列上，
    导入时又被当成非法日期丢掉，越看越像系统丢数据。
    """
    content = csv_bytes(
        [
            [
                "示例：宁波宏远包装制品有限公司",  # 客户名称
                "宏远包装",                        # 客户简称
                "A",                               # 客户等级
                "浙江",                            # 省份
                "浙江省宁波市…",                   # 详细地址
                "展会",                            # 客户来源
                "zhangsan",                        # 负责人登录名
                "2025-03-18",                      # 最后联系日期
                "历史名单导入，联系时间来自老系统",  # 备注
            ]
        ],
        io_util.TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customer-import-template.csv"},
    )


async def _export(
    user,
    session: AsyncSession,
    stmt: Select | None = None,
    request: Request | None = None,
    *,
    purpose: CustomerExportPurpose,
    purpose_note: str | None = None,
    filters: dict | None = None,
) -> Response:
    """共用导出实现：查行 → 补负责人名 → 生成 CSV。

    数据范围由调用方加进 `stmt`（两个入口都加了，不能漏）。
    导出闸门（§11.2/场景19）：独立权限在路由层把住；这里做规模阈值
    和导出审计——"能看列表"不再等于"能批量拿走本范围全部客户"。
    """
    if stmt is None:
        stmt = await svc.apply_data_scope(
            svc.not_deleted(svc.build_list_stmt()), user, session
        )
    rows = (await session.execute(stmt)).scalars().all()
    from app.modules.settings import service as settings_service

    export_limit = int(await settings_service.get_number(session, "export", "limit", 5000))
    if len(rows) > export_limit:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"本次导出 {len(rows)} 条，超过单次上限 {export_limit}。"
            "请缩小筛选范围，或联系管理员调整 export.limit 配置",
            413,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="export",
        business_type="customer",
        after={
            "purpose": purpose.value,
            "purpose_note": purpose_note,
            "count": len(rows),
            "data_scope": user.data_scope,
            "filters": filters or {},
            "customer_ids": [row.id for row in rows[:200]],
            "customer_ids_truncated": len(rows) > 200,
        },
        ip=client_ip(request) if request else None,
    )
    await _alert_if_abnormal(session, user=user, ip=client_ip(request) if request else None)
    await session.commit()
    owner_ids = {row.owner_id for row in rows if row.owner_id}
    owners: dict[int, str] = {}
    if owner_ids:
        owner_rows = (
            await session.execute(
                select(User.id, User.name).where(User.id.in_(owner_ids))
            )
        ).all()
        owners = {int(uid): name for uid, name in owner_rows}

    content = csv_bytes(
        [
            io_util.customer_export_row(
                row, owners.get(row.owner_id) if row.owner_id else None
            )
            for row in rows
        ],
        io_util.EXPORT_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customers.csv"},
    )


async def _alert_if_abnormal(session: AsyncSession, *, user, ip: str | None) -> None:
    """异常批量访问告警（文档 §六）：同一人在窗口期内累计导出条数超阈值 → 推管理员。

    只在导出动作之后检查，命中写一条 export_alert 审计并站内推给管理员；
    不阻断业务（该拦的大单条导出已被 export.limit 拦）。阈值与窗口可配。
    """
    from datetime import UTC, datetime, timedelta

    from app.core.audit import AuditLog
    from app.modules.notification import service as notification_service
    from app.modules.settings import service as settings_service

    alert_rows = int(await settings_service.get_number(session, "export", "alert_rows", 20000))
    window_hours = int(
        await settings_service.get_number(session, "export", "alert_window_hours", 24)
    )
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    # 导出条数存在审计的 after_data['count'] 里（JSONB），窗口内条数很少，直接取回再累加
    payloads = (
        await session.execute(
            select(AuditLog.after_data).where(
                AuditLog.operator_id == user.id,
                AuditLog.action == "export",
                AuditLog.business_type == "customer",
                AuditLog.created_at >= since,
            )
        )
    ).scalars().all()
    total = sum(int((payload or {}).get("count") or 0) for payload in payloads)
    if total > alert_rows:
        await write_audit(
            session,
            operator_id=user.id,
            action="export_alert",
            business_type="customer",
            after={
                "window_hours": window_hours,
                "rows_in_window": total,
                "threshold": alert_rows,
            },
            ip=ip,
        )
        try:
            await notification_service.notify_roles(
                session,
                role_codes=["admin"],
                type_="system",
                title="异常批量导出提醒",
                content=(
                    f"{getattr(user, 'name', '某用户')} 在 {window_hours} 小时内累计导出客户 "
                    f"{total} 条，超过阈值 {alert_rows}，请核实用途"
                ),
                business_type="customer",
                business_id=None,
                exclude_user_id=user.id,
            )
        except Exception:  # noqa: BLE001 —— 告警失败不能挡住导出本身
            pass


@router.get("/customers/export")
async def export_customers(
    request: Request,
    purpose: CustomerExportPurpose,
    purpose_note: str | None = Query(default=None, max_length=200),
    # 导出闸门（§11.2/场景19）：批量导出是独立授权，与 customer:view 分开——
    # "能看列表"不再等于"能批量拿走本范围全部客户"。admin 角色默认放行
    user: CurrentUser = Depends(require_permission("customer:export")),
    session: AsyncSession = Depends(get_db),
):
    """导出当前用户数据范围内的全部客户。"""
    if purpose == CustomerExportPurpose.OTHER and not (purpose_note or "").strip():
        raise AppError(ErrorCode.PARAM_ERROR, "用途选择“其他”时，补充说明必填", 422)
    if purpose != CustomerExportPurpose.OTHER and (purpose_note or "").strip():
        raise AppError(ErrorCode.PARAM_ERROR, "仅用途选择“其他”时填写补充说明", 422)
    return await _export(
        user, session, request=request, purpose=purpose,
        purpose_note=(purpose_note or "").strip() or None,
    )


@router.post("/customers/export")
async def export_customers_filtered(
    payload: CustomerExportFilter,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:export")),
    session: AsyncSession = Depends(get_db),
):
    """按筛选条件导出客户（03-API §7 `POST /customers/export`）。

    筛选条件放 body，参数与列表页一致 —— "列表页筛出什么就导出什么"。
    数据范围仍然强制生效。
    """
    stmt = await svc.apply_data_scope(
        svc.not_deleted(
            svc.build_list_stmt(
                keyword=payload.keyword,
                level=payload.level,
                status=payload.status,
                source=payload.source,
                owner_id=payload.owner_id,
                pool_status=payload.pool_status,
            )
        ),
        user,
        session,
    )
    filters = {
        "keyword_applied": bool(payload.keyword and payload.keyword.strip()),
        "level": payload.level,
        "status": payload.status,
        "source": payload.source,
        "owner_id": payload.owner_id,
        "pool_status": payload.pool_status,
    }
    filters = {key: value for key, value in filters.items() if value is not None and value is not False}
    return await _export(
        user,
        session,
        stmt=stmt,
        request=request,
        purpose=payload.purpose,
        purpose_note=payload.purpose_note,
        filters=filters,
    )


@router.post("/customers/import")
async def import_customers(
    request: Request,
    file: UploadFile = File(...),
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("customer:create")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入客户：逐行查重，疑似重复照样建档并开待裁定单。

    第七批 7.1 / 7.5 的返修点：
    - 每行一个 SAVEPOINT：坏行只回滚自己，不会让后续正常行一起失败；
    - 非法/未来日期明确报错（不再静默丢成"没填"）；
    - 负责人登录名写错、账号已停用 → 这一行失败，不静默归到导入人名下；
    - 没有联系日期的客户标 `last_contact_unknown`，**不参与自动回收扫描**，
      等补核（用户 2026-10-06 确认的口径）；
    - 支持 `preview=1`，并在预览里回一份**负责人映射表**供人工核对。
    """
    from app.core.data_scope import scoped_owner_ids

    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(raw, required_headers=["客户名称"], label="文件")
    report = ImportReport("customer", len(rows))

    # 撞单比对本身**仍然全量**：跨部门的重复不能静默放过，否则导入那一刻就按
    # "谁先建档"把归属定了。但要不要**回显候选客户的身份**取决于调用者的范围——
    # 否则这个接口就等于"用 Excel 批量试探全公司客户名"。明细在撞单裁定页，
    # 由有范围的主管看。
    scope = await scoped_owner_ids(session, user)
    #: 负责人映射表：预览时人要看的就是它（哪个登录名落到了谁，谁没匹配上）
    owner_mapping: dict[str, dict] = {}
    unknown_contact_rows: list[dict] = []

    for index, row in enumerate(rows, start=2):  # 第 1 行是表头
        name = (row.get("客户名称") or "").strip()
        errs = RowErrors(index, name)
        if not name:
            errs.add("客户名称不能为空")
        elif len(name) > 200:
            errs.add(f"客户名称长度不能超过 200（当前 {len(name)}）")

        last_contact, date_error = io_util.parse_date_checked(row.get("最后联系日期"))
        if date_error:
            errs.add(date_error)

        owner_id, owner_error, owner_info = await io_util.resolve_owner_checked(
            session, row.get("负责人登录名"), user.id
        )
        if owner_error:
            errs.add(owner_error)
        elif owner_info:
            key = str(owner_info.get("login") or "(未填负责人)")
            owner_mapping.setdefault(
                key,
                {
                    "login": owner_info.get("login"),
                    "owner_id": owner_info.get("owner_id"),
                    "owner_name": owner_info.get("owner_name"),
                    "resolved": owner_info.get("resolved"),
                    "note": owner_info.get("note"),
                },
            )

        if errs:
            report.failed_row(index, name, errs.reasons)
            continue

        try:
            async with row_savepoint(session):
                duplicates = await find_duplicate_customers(
                    session,
                    company_name=name,
                    mobile=None,
                    tax_no=None,
                    domain=None,
                    limit=1,
                )
                # 撞单**不再丢行**（文档 §11.5 :269「历史导入客户不能一律被先建档者
                # 占有」）：照样建档，同时开待裁定单、进争议冻结，归属等主管裁定。
                # 以前这里直接 continue，等于导入那一刻就按"谁先建档"把归属定了，
                # 正是文档点名要避免的。
                if duplicates:
                    top = duplicates[0]
                    report.disputed_row(
                        index,
                        name,
                        # 范围不足的调用者只得到"这是重复"，拿不到对方是谁
                        candidate=None if scope is not None else top.get("name"),
                        score=None if scope is not None else top.get("score"),
                        reasons=None if scope is not None else top.get("reasons"),
                        note=(
                            "疑似与库内已有客户重复，已开待裁定单，由主管裁定"
                            if scope is not None
                            else None
                        ),
                    )

                # 老数据迁移（§六 :167）：文件里给了历史联系时间就按真实的写，
                # 不覆盖成"今天"。没给则标成"联系时间未知"（不参与自动回收），
                # 等业务补核 —— 不能拿导入时间冒充真实联系时间。
                customer = Customer(
                    name=name,
                    short_name=(row.get("客户简称") or "").strip() or None,
                    level=(row.get("客户等级") or "").strip() or None,
                    region=(row.get("省份") or "").strip() or None,
                    address=(row.get("详细地址") or "").strip() or None,
                    source=(row.get("客户来源") or "").strip() or "Excel 导入",
                    remark=(row.get("备注") or "").strip() or None,
                    customer_type="企业",
                    country="中国",
                    status="active",
                    pool_status="private",
                    owner_id=owner_id,
                    created_by=user.id,
                    last_followup_at=last_contact,
                    last_contact_unknown=last_contact is None,
                )
                session.add(customer)
                await session.flush()
                if report.disputed and report.disputed[-1].get("row") == index:
                    from app.modules.customer import duplicates as dup_service

                    await dup_service.open_cases_for_customer(
                        session, customer=customer, source="import", actor_id=user.id
                    )
                customer_id = customer.id
                customer_name = customer.name
            report.created_row(
                index,
                name,
                id=customer_id,
                # 带没带上历史联系时间要能核对——迁移验收就看这个数
                last_followup_at=(
                    last_contact.date().isoformat() if last_contact else None
                ),
                last_contact_unknown=last_contact is None,
                owner_id=owner_id,
            )
            if last_contact is None:
                unknown_contact_rows.append(
                    {"row": index, "id": customer_id, "name": customer_name}
                )
        except Exception as exc:  # 单行失败不影响其它行
            report.failed_row(index, name, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="customer",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="customer",
        extra={
            # 负责人映射表：预览时先看这里，别等导完才发现登录名写错
            "owner_mapping": list(owner_mapping.values()),
            "unknown_contact_count": len(unknown_contact_rows),
            "unknown_contact": unknown_contact_rows[:200],
        },
    )
