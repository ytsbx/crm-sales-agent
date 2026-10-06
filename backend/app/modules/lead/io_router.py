"""线索导入导出与软删/恢复（对齐 03-API §6）。

单独成 router 的原因和客户一样：`/leads/import`、`/leads/export` 这类
静态路径必须注册在 `/leads/{lead_id}` 之前，否则会被动态路由抢先匹配
（`/leads/export` 会被当成 lead_id="export" 解析失败）。
"""

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.importing import (
    ImportReport,
    RowErrors,
    RowSkipped,
    finalize,
    row_savepoint,
)
from app.core.response import ok
from app.modules.lead import io as io_util
from app.modules.lead import service as svc
from app.modules.lead.model import Lead
from app.modules.lead.schema import LeadExportFilter
from app.modules.user.model import User

router = APIRouter(tags=["Lead"])


async def _owner_map(session: AsyncSession, lead_rows: list[Lead]) -> dict[int, str]:
    ids = {row.owner_id for row in lead_rows if row.owner_id}
    if not ids:
        return {}
    rows = (
        await session.execute(select(User.id, User.name).where(User.id.in_(ids)))
    ).all()
    return {int(uid): name for uid, name in rows}


@router.get("/leads/import-template")
async def import_template(
    _: CurrentUser = Depends(require_permission("lead:create")),
):
    """下载线索导入模板。"""
    content = csv_bytes(
        [["示例：宁波宏远有包装采购需求", "宁波宏远包装制品有限公司", "陈经理",
          "13700000003", "chen@example.com", "浙江", "展会", "zhangsan", "备注"]],
        io_util.TEMPLATE_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''lead-import-template.csv"},
    )


async def _export(user, session: AsyncSession, stmt) -> Response:
    rows = (await session.execute(stmt)).scalars().all()
    owners = await _owner_map(session, rows)
    content = csv_bytes(
        [
            io_util.lead_export_row(row, owners.get(row.owner_id) if row.owner_id else None)
            for row in rows
        ],
        io_util.EXPORT_HEADERS,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''leads.csv"},
    )


@router.get("/leads/export")
async def export_leads(
    include_deleted: bool = False,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """导出当前用户数据范围内的线索。`include_deleted=true` 带上回收站的。"""
    stmt = await svc.apply_data_scope(
        svc.build_lead_stmt(include_deleted=include_deleted), user, session
    )
    return await _export(user, session, stmt)


@router.post("/leads/export")
async def export_leads_filtered(
    payload: LeadExportFilter,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """按筛选条件导出线索（03-API §6）。

    参数与列表页一致 —— "列表页筛出什么就导出什么"。
    """
    stmt = await svc.apply_data_scope(
        svc.build_lead_stmt(
            keyword=payload.keyword,
            status=payload.status,
            source=payload.source,
            owner_id=payload.owner_id,
            region=payload.region,
            include_deleted=payload.include_deleted,
        ),
        user,
        session,
    )
    return await _export(user, session, stmt)


@router.post("/leads/import")
async def import_leads(
    request: Request,
    file: UploadFile = File(...),
    preview: bool = Form(False),
    preview_token: str | None = Form(None),
    user: CurrentUser = Depends(require_permission("lead:create")),
    session: AsyncSession = Depends(get_db),
):
    """批量导入线索。

    查重口径与客户不同：线索只跳过"公司名或手机号**完全相同**"的行。
    线索是粗筛名单，套客户的相似度阈值会把大量真实新线索挡在门外。

    第七批 7.1 / 7.6：每行一个 SAVEPOINT（坏行不再把后续行一起拖死）、
    支持 `preview=1`、失败清单全量返回且按唯一行号计数。
    """
    raw = await file.read()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    rows = parse_csv_bytes(raw, required_headers=["线索名称"], label="文件")
    report = ImportReport("lead", len(rows))

    for index, row in enumerate(rows, start=2):  # 第 1 行是表头
        name = (row.get("线索名称") or "").strip()
        company = (row.get("公司名称") or "").strip() or None
        mobile = (row.get("手机号") or "").strip() or None
        errs = RowErrors(index, name)
        if not name:
            errs.add("线索名称不能为空")
        if errs:
            report.failed_row(index, name, errs.reasons)
            continue

        try:
            async with row_savepoint(session):
                conditions = []
                if company:
                    conditions.append(Lead.company_name == company)
                if mobile:
                    conditions.append(Lead.mobile == mobile)
                if conditions:
                    duplicate = (
                        await session.execute(
                            select(Lead).where(
                                Lead.deleted_at.is_(None), or_(*conditions)
                            ).limit(1)
                        )
                    ).scalars().first()
                    if duplicate is not None:
                        raise RowSkipped(
                            f"已存在同名公司或同号线索（id={duplicate.id}）"
                        )

                owner_id = await svc.resolve_owner(
                    session, row.get("负责人登录名"), user.id
                )
                lead = Lead(
                    name=name,
                    company_name=company,
                    contact_name=(row.get("联系人") or "").strip() or None,
                    mobile=mobile,
                    email=(row.get("邮箱") or "").strip() or None,
                    region=(row.get("省份") or "").strip() or None,
                    source=(row.get("来源") or "").strip() or "Excel 导入",
                    remark=(row.get("备注") or "").strip() or None,
                    country="中国",
                    status="pending",
                    owner_id=owner_id,
                    created_by=user.id,
                )
                session.add(lead)
                await session.flush()
                lead_id = lead.id
            report.created_row(index, name, id=lead_id)
        except RowSkipped as skipped:
            report.skipped_row(index, name, str(skipped))
        except Exception as exc:  # 单行失败不影响其它行
            report.failed_row(index, name, f"写入失败：{str(exc)[:160]}")

    return await finalize(
        session,
        report,
        module="lead",
        operator_id=user.id,
        file_name=file.filename,
        file_sha256=file_sha256,
        ip=client_ip(request),
        preview=preview,
        preview_token=preview_token,
        business_type="lead",
    )


@router.delete("/leads/{lead_id}")
async def delete_lead(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    """软删线索（进回收站）。

    已经转化过的线索不给删 —— 转化的客户/联系人/商机指着它，
    删掉会让"这条客户从哪来的"断线。要清理就走废弃（discard）。
    """
    lead = await svc.get_visible_lead(session, user, lead_id)
    if lead.converted_customer_id is not None or lead.status == "converted":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该线索已转化，不能删除；如需终止请使用废弃",
        )
    lead.deleted_at = datetime.now(UTC)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="lead",
        business_id=lead.id,
        before=svc.serialize_lead(lead),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "线索已删除，可在回收站恢复")


@router.post("/leads/{lead_id}/restore")
async def restore_lead(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    """从回收站恢复线索。"""
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)
    if lead.deleted_at is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该线索没有被删除，无需恢复")
    # 恢复也是写操作，同样要过数据范围（先恢复再校验会让越权者得手）
    await svc.assert_lead_visible(session, user, lead)
    lead.deleted_at = None
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="restore",
        business_type="lead",
        business_id=lead.id,
        after=svc.serialize_lead(lead),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_lead(lead), "线索已恢复")
