"""客户标签、合并与查重接口（03-API §7）。

对应文档里此前缺失的 6 个路径 + 一个标签字典接口：
  GET    /tags                            标签字典
  POST   /tags                            新建标签
  PATCH  /tags/{tag_id}                   编辑标签
  DELETE /tags/{tag_id}                   删除标签
  POST   /customers/{id}/tags             给客户打标签
  DELETE /customers/{id}/tags/{tag_id}    摘除标签
  POST   /customers/deduplicate           查重（打分，返回疑似列表）
  POST   /customers/merge                 合并
  POST   /customers/batch-tag             批量打标签
  POST   /customers/batch-transfer        批量转移负责人

注册顺序：`/tags`、`/customers/deduplicate`、`/customers/merge`、`/customers/batch-*`
这些静态路径必须排在 customer_router 的 `/customers/{id}` 之前，
所以本路由在 main.py 里注册在 customer_router 之前（和 io_router 同理）。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.contact_util import find_duplicate_customers
from app.modules.customer import service as svc
from app.modules.customer import tags as tag_svc
from app.modules.customer.model import Customer
from app.modules.customer.tags_schema import (
    CustomerBatchTag,
    CustomerBatchTransfer,
    CustomerMergeRequest,
    CustomerTagAttach,
    TagCreate,
    TagUpdate,
)

router = APIRouter(tags=["Customer"])


# ---------------------------------------------------------------- 标签字典

@router.get("/tags")
async def list_tags(
    only_active: bool = False,
    _: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = await tag_svc.list_tags(session, only_active=only_active)
    return ok([tag_svc.serialize_tag(tag) for tag in rows])


@router.post("/tags")
async def create_tag(
    payload: TagCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    tag = await tag_svc.create_tag(
        session, name=payload.name, type_=payload.type, sort_no=payload.sort_no
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="tag",
        business_id=tag.id,
        after=tag_svc.serialize_tag(tag),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(tag_svc.serialize_tag(tag), "标签已创建")


@router.patch("/tags/{tag_id}")
async def update_tag(
    tag_id: int,
    payload: TagUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    tag = await tag_svc.get_tag_or_404(session, tag_id)
    before = tag_svc.serialize_tag(tag)
    tag = await tag_svc.update_tag(session, tag, payload.model_dump(exclude_unset=True))
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="tag",
        business_id=tag.id,
        before=before,
        after=tag_svc.serialize_tag(tag),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(tag_svc.serialize_tag(tag), "已保存")


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    tag = await tag_svc.get_tag_or_404(session, tag_id)
    before = tag_svc.serialize_tag(tag)
    await tag_svc.delete_tag(session, tag)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="tag",
        business_id=tag_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "标签已删除")


# ---------------------------------------------------------------- 客户查重与合并

@router.post("/customers/deduplicate")
async def deduplicate(
    payload: dict,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """客户查重：按名称/手机/税号/域名/地址加权打分，返回疑似重复列表。

    可直接传已有客户的 id（编辑场景），也可传待建客户的字段（新建场景）。
    """
    customer_id = payload.get("customer_id")
    fields = {
        "company_name": payload.get("name") or payload.get("company_name"),
        "mobile": payload.get("mobile"),
        "tax_no": payload.get("tax_no"),
        "domain": payload.get("domain"),
        "address": payload.get("address"),
    }
    exclude_id = None

    if customer_id:
        existing = await session.get(Customer, customer_id)
        if existing is None or existing.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
        exclude_id = existing.id
        fields = {
            "company_name": existing.name,
            "mobile": fields["mobile"],
            "tax_no": existing.tax_no,
            "domain": existing.domain,
            "address": existing.address,
        }

    if not any(fields.values()):
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            "至少要提供名称、手机号、税号、域名、地址中的一项才能查重",
        )

    matches = await find_duplicate_customers(session, **fields)
    # 编辑自己时把自己排除掉，否则永远提示"和自己重复"
    if exclude_id is not None:
        matches = [m for m in matches if m.get("id") != exclude_id]

    return ok({"matches": matches, "count": len(matches)})


@router.post("/customers/merge")
async def merge_customers(
    payload: CustomerMergeRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """把来源客户合并进目标客户（不可逆，先存快照）。"""
    source = await svc.get_visible_customer(session, user, payload.source_customer_id)
    target = await svc.get_visible_customer(session, user, payload.target_customer_id)

    result = await tag_svc.merge_customers(
        session,
        source=source,
        target=target,
        operator_id=user.id,
        reason=payload.reason,
    )

    await write_audit(
        session,
        operator_id=user.id,
        action="merge",
        business_type="customer",
        business_id=target.id,
        before={"source_customer_id": source.id, "source_snapshot": result["snapshot"]},
        after={"target_customer_id": target.id, "moved": result["moved"]},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "target_customer_id": target.id,
            "source_customer_id": source.id,
            "moved": result["moved"],
            "merge_log_id": result["merge_log_id"],
        },
        f"已将「{result['snapshot']['name']}」合并进「{target.name}」",
    )


# ---------------------------------------------------------------- 客户打标签

@router.post("/customers/{customer_id}/tags")
async def attach_customer_tags(
    customer_id: int,
    payload: CustomerTagAttach,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    added = await tag_svc.attach_tags(session, customer.id, payload.tag_ids)
    await write_audit(
        session,
        operator_id=user.id,
        action="tag",
        business_type="customer",
        business_id=customer.id,
        after={"added_tag_ids": payload.tag_ids},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"added": added}, "标签已更新")


@router.delete("/customers/{customer_id}/tags/{tag_id}")
async def detach_customer_tag(
    customer_id: int,
    tag_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_visible_customer(session, user, customer_id)
    await tag_svc.detach_tag(session, customer.id, tag_id)
    await write_audit(
        session,
        operator_id=user.id,
        action="untag",
        business_type="customer",
        business_id=customer.id,
        before={"tag_id": tag_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "标签已移除")


@router.post("/customers/batch-tag")
async def batch_tag(
    payload: CustomerBatchTag,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    """批量打标签。mode: add 追加 / replace 覆盖 / remove 摘除。"""
    if not payload.customer_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请先选择客户")
    if payload.mode not in ("add", "replace", "remove"):
        raise AppError(ErrorCode.PARAM_ERROR, "mode 只能是 add / replace / remove", 422)
    if not payload.tag_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请先选择标签")

    affected = 0
    for customer_id in dict.fromkeys(payload.customer_ids):
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            continue
        if payload.mode == "remove":
            for tag_id in payload.tag_ids:
                await tag_svc.detach_tag(session, customer.id, tag_id)
        else:
            if payload.mode == "replace":
                existing = await tag_svc.tags_of_customers(session, [customer.id])
                for tag in existing.get(customer.id, []):
                    await tag_svc.detach_tag(session, customer.id, tag["id"])
            await tag_svc.attach_tags(session, customer.id, payload.tag_ids)
        affected += 1

    await write_audit(
        session,
        operator_id=user.id,
        action=f"batch_tag_{payload.mode}",
        business_type="customer",
        business_id=None,
        after={"customer_ids": payload.customer_ids, "tag_ids": payload.tag_ids},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"affected": affected}, f"已处理 {affected} 个客户")


@router.post("/customers/batch-transfer")
async def batch_transfer(
    payload: CustomerBatchTransfer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """批量转移负责人；owner_id 为空表示放入公海。

    目标负责人的存在性/在职校验放在 svc.transfer_customer 里，单个与批量共用同一条规则。
    """
    if not payload.customer_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请先选择客户")

    affected = 0
    for customer_id in dict.fromkeys(payload.customer_ids):
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            continue
        if customer.owner_id == payload.owner_id:
            continue
        await svc.transfer_customer(
            session, user, customer, payload.owner_id, payload.reason or "批量转移"
        )
        affected += 1

    await write_audit(
        session,
        operator_id=user.id,
        action="batch_transfer",
        business_type="customer",
        business_id=None,
        after={"customer_ids": payload.customer_ids, "owner_id": payload.owner_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"affected": affected}, f"已转移 {affected} 个客户")


@router.get("/customers/{customer_id}/merge-logs")
async def customer_merge_logs(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_customer(session, user, customer_id)
    return ok(await tag_svc.merge_logs(session, customer_id))


__all__ = ["router", "Query"]


# ---------------------------------------------------------------- 撞单裁定（验收 20）

@router.get("/customer-duplicate-cases")
async def list_duplicate_cases(
    status: str | None = Query("pending"),
    limit: int = Query(100, ge=1, le=300),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """撞单待裁定队列。系统只摆证据，归属由人写。"""
    from app.modules.customer import duplicates as dup_service

    return ok(await dup_service.list_cases(session, status=status, limit=limit))


@router.post("/customers/{customer_id}/duplicate-cases")
async def open_duplicate_cases(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """对一条客户再跑一次查重并开待裁定单（幂等：同一对只留一张未决单）。"""
    from app.modules.customer import duplicates as dup_service

    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    cases = await dup_service.open_cases_for_customer(
        session, customer=customer, source="manual", actor_id=user.id
    )
    await session.commit()
    return ok({"opened": len(cases)}, f"已开 {len(cases)} 条待裁定")


@router.post("/customer-duplicate-cases/{case_id}/resolve")
async def resolve_duplicate_case(
    case_id: int,
    payload: dict,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """裁定撞单：判为不同客户 / 归已有客户负责人 / 归新客户负责人。

    归属由人指定，代码**不按建档先后推导**（文档 §11.4 对场景 20 的原话）。
    """
    from app.modules.customer import duplicates as dup_service
    from app.modules.customer.model import CustomerDuplicateCase

    case = await session.get(CustomerDuplicateCase, case_id)
    if case is None:
        raise AppError(ErrorCode.NOT_FOUND, "撞单记录不存在", 404)
    await dup_service.resolve_case(
        session,
        case=case,
        decision=str(payload.get("decision") or ""),
        owner_id=payload.get("owner_id"),
        remark=payload.get("remark"),
        actor_id=user.id,
    )
    names = {}
    for cid in (case.customer_id, case.candidate_id):
        row = await session.get(Customer, cid)
        if row is not None:
            names[cid] = row.name
    result = dup_service.serialize_case(case, names)
    await write_audit(
        session,
        operator_id=user.id,
        action="resolve_customer_duplicate",
        business_type="customer_duplicate_case",
        business_id=case.id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, "裁定已登记")
