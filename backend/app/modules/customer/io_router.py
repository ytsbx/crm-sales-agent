"""客户导入导出接口。"""

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.csvio import csv_bytes
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
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


async def _export(user, session: AsyncSession, stmt: Select | None = None) -> Response:
    """共用导出实现：查行 → 补负责人名 → 生成 CSV。

    数据范围由调用方加进 `stmt`（两个入口都加了，不能漏）。
    """
    if stmt is None:
        stmt = await svc.apply_data_scope(
            svc.not_deleted(svc.build_list_stmt()), user, session
        )
    rows = (await session.execute(stmt)).scalars().all()
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


@router.get("/customers/export")
async def export_customers(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """导出当前用户数据范围内的全部客户。"""
    return await _export(user, session)


@router.post("/customers/export")
async def export_customers_filtered(
    payload: CustomerExportFilter,
    user: CurrentUser = Depends(require_permission("customer:view")),
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
    return await _export(user, session, stmt=stmt)


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
            )
            session.add(customer)
            await session.flush()
            created.append({"row": index, "id": customer.id, "name": customer.name})
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
