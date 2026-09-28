"""客户导入导出接口。"""

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.csvio import csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.contact_util import find_duplicate_customers
from app.modules.customer import io as io_util
from app.modules.customer import service as svc
from app.modules.customer.model import Customer
from app.modules.customer.schema import CustomerExportFilter
from app.modules.user.model import User

router = APIRouter(tags=["Customer"])


@router.get("/customers/import-template")
async def import_template(
    _: CurrentUser = Depends(require_permission("customer:create")),
):
    """下载导入模板：表头 + 一行示例。"""
    content = csv_bytes(
        [["示例：宁波宏远包装制品有限公司", "宏远包装", "A", "浙江", "浙江省宁波市…", "展会", "zhangsan", "备注"]],
        io_util.TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''customer-import-template.csv"},
    )


async def _export(
    user, session: AsyncSession, stmt: Select | None = None, request: Request | None = None
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
            "count": len(rows),
            "filtered": stmt is not None,
            "customer_ids": [row.id for row in rows[:200]],
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
    # 导出闸门（§11.2/场景19）：批量导出是独立授权，与 customer:view 分开——
    # "能看列表"不再等于"能批量拿走本范围全部客户"。admin 角色默认放行
    user: CurrentUser = Depends(require_permission("customer:export")),
    session: AsyncSession = Depends(get_db),
):
    """导出当前用户数据范围内的全部客户。"""
    return await _export(user, session, request=request)


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
    return await _export(user, session, stmt=stmt, request=request)


@router.post("/customers/import")
async def import_customers(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("customer:create")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入客户：逐行查重，疑似重复跳过并报告。"""
    rows = await io_util.parse_upload(file)
    created: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for index, row in enumerate(rows, start=2):  # 第 1 行是表头
        name = (row.get("客户名称") or "").strip()
        try:
            duplicates = await find_duplicate_customers(
                session,
                company_name=name,
                mobile=None,
                tax_no=None,
                domain=None,
                limit=1,
            )
            if duplicates:
                top = duplicates[0]
                skipped.append(
                    {
                        "row": index,
                        "name": name,
                        "reason": f"疑似重复：{top['name']}（{top['score']} 分，{'、'.join(top['reasons'])}）",
                    }
                )
                continue

            owner_id = await io_util.resolve_owner(
                session, row.get("负责人登录名"), user.id
            )
            # 老数据迁移（§六 :167）：文件里给了历史联系时间就按真实的写，
            # 不覆盖成"今天"——否则这批客户进系统当天全算活跃，冷落预警
            # 要等一整个周期才生效。没给或填错则留空，落回"刚建档"。
            last_contact = io_util.parse_date(row.get("最后联系日期"))
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
            )
            session.add(customer)
            await session.flush()
            created.append(
                {
                    "row": index,
                    "id": customer.id,
                    "name": customer.name,
                    # 带没带上历史联系时间要能核对——迁移验收就看这个数
                    "last_followup_at": (
                        customer.last_followup_at.date().isoformat()
                        if customer.last_followup_at
                        else None
                    ),
                }
            )
        except Exception as exc:  # 单行失败不影响其它行
            failed.append({"row": index, "name": name, "reason": str(exc)[:120]})

    await write_audit(
        session,
        operator_id=user.id,
        action="import",
        business_type="customer",
        after={"created": len(created), "skipped": len(skipped), "failed": len(failed)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "total": len(rows),
            "created_count": len(created),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "created": created[:50],
            "skipped": skipped[:50],
            "failed": failed[:50],
        },
        f"导入完成：成功 {len(created)} 条，跳过疑似重复 {len(skipped)} 条，失败 {len(failed)} 条",
    )
