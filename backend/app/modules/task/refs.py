"""任务的业务关联校验 —— 普通建任务与「补建后续任务」**共用这一份**。

## 为什么必须共用（第十一批 11.6 复审）

任务上的 `customer_id / contact_id / lead_id / opportunity_id / quote_id / order_id`
在库里**都没有外键**（都是普通 BigInteger 列），写进去就写进去了，数据库不兜底。
两个入口各写一套校验，必然一套全、一套缺 —— 复审实测：
普通建任务挂「别人的报价」会 403，而"从历史跟进补建任务"同一条报价照样写进新任务，
跨客户联系人/商机、已删除的线索也原样照抄。

## 每个关联都要过三关

1. **在不在**：行不存在、或已软删 → 不合格；
2. **归不归我**：`ensure_in_scope`（客户与线索**允许无主** —— 公海、线索池本就无主）；
3. **是不是同一个客户**：单据自带 `customer_id`，与这张任务最终确定的客户对不上就不合格。

## 两种失败方式，由 `strict` 选

- `strict=True`（普通建任务）：**当场报错**。用户正在弹窗里挑关联，应当立刻知道哪一项不对；
- `strict=False`（补建后续任务）：**跳过**。关联是从历史数据**继承**来的，用户并没有在
  "选关联"，不该因为一条历史脏数据让整件事做不成。跳过的要在返回里列出来。

⚠️ 跳过时的说明**只写"哪一类、哪一号、为什么"，绝不写受限对象的名称或内容** ——
任务列表是给普通人看的，把"报价单「某某客户年度大单价」不在你的范围内"印上去，
等于用一次补建动作泄露了别人的商业信息（主人明确要求）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest

#: 关联类型 → (中文名, 模型, 是否允许无主)。
#: 「允许无主」只给客户（公海）、线索（线索池）、打样（没有负责人这一栏）——
#: 这几类"没有归属人"是正常形态；其余（商机/报价/订单）必须有主，
#: 且要落在调用者的数据范围内。
_REF_SPECS: tuple[tuple[str, str, type, bool], ...] = (
    ("customer", "客户", Customer, True),
    ("lead", "线索", Lead, True),
    ("opportunity", "商机", Opportunity, False),
    ("quote", "报价单", Quote, False),
    ("order", "订单", SalesOrder, False),
    # 打样比较特殊：任务表**没有 sample 列**，它落在 `source_business_*` 上
    # （任务详情据此跳转）。校验照样要过，因为"记着来源"同样会把人带过去。
    ("sample", "打样", SampleRequest, True),
)


async def normalize_task_refs(
    session: AsyncSession,
    user: CurrentUser,
    payload: dict,
    *,
    strict: bool,
    base_customer_id: int | None = None,
) -> tuple[dict, list[str]]:
    """校验并归一化任务入参里的业务关联。

    :param payload: 含 `*_id` 的入参字典（**不会被就地修改**，返回的是副本）
    :param strict: True=不合格就报错；False=不合格就摘掉并记进 skipped
    :param base_customer_id: 调用方已确定的客户（补建任务时来自原跟进）。
        `None` = 由关联自己推断。
    :returns: (净化后的入参, 被跳过的说明列表)

    ⚠️ 归一化会**改写 `customer_id`**：这是刻意的 —— 关联单据自带的客户才是真相，
    调用方传的那个只是"用户选的"。客户范围校验放在最后统一做一次，因为
    推断出来的客户同样不能绕过数据范围。
    """
    data = dict(payload)
    skipped: list[str] = []

    async def _check(kind: str, label: str, model: type, obj_id: int,
                     allow_unowned: bool) -> tuple[bool, str, int | None]:
        """三关：在不在 → 归不归我 → （返回它自带的客户 id）。"""
        obj = await session.get(model, obj_id)
        if obj is None or getattr(obj, "deleted_at", None) is not None:
            return False, "已不存在或已被删除", None
        try:
            # `getattr` 而不是直接取属性：不是每个模型都有 `owner_id`
            # （打样单就没有），取不到时按"无主"处理，由 `allow_unowned` 决定放不放。
            await ensure_in_scope(session, user, owner_id=getattr(obj, "owner_id", None),
                                  label=label, allow_unowned=allow_unowned)
        except AppError:
            # 严格模式把这个 403 原样抛出去（它的文案本来就只说类别、不点名字）
            if strict:
                raise
            return False, "不在你的数据范围内", None
        return True, "", getattr(obj, "customer_id", None)

    customer_id: int | None = (
        base_customer_id if base_customer_id is not None else data.get("customer_id")
    )

    for kind, label, model, allow_unowned in _REF_SPECS:
        obj_id = data.get(f"{kind}_id")
        if not obj_id:
            continue
        ok, why, linked = await _check(kind, label, model, int(obj_id), allow_unowned)
        if not ok:
            if strict:
                # 走到这里只可能是"不存在"那一档（范围拒绝已在 _check 里抛掉了）
                raise AppError(ErrorCode.NOT_FOUND, f"{label} id={obj_id} 不存在", 404)
            data[f"{kind}_id"] = None
            skipped.append(f"{label} #{obj_id}（{why}）")
            continue
        if kind == "customer":
            customer_id = int(obj_id)
            continue
        if linked:
            if customer_id and int(customer_id) != int(linked):
                if strict:
                    raise AppError(
                        ErrorCode.PARAM_ERROR,
                        "待办所选的客户与关联单据不是同一家客户，请确认后再保存",
                        422,
                    )
                data[f"{kind}_id"] = None
                skipped.append(f"{label} #{obj_id}（不属于同一个客户）")
                continue
            customer_id = int(linked)

    # 联系人：它自己也能**确定**客户（"只传联系人"是一条真实路径，
    # 原实现漏掉它时那条路径整段跳过了校验）。
    if data.get("contact_id") is not None:
        contact_id = int(data["contact_id"])
        contact = await session.get(Contact, contact_id)
        if contact is None or contact.deleted_at is not None:
            if strict:
                raise AppError(ErrorCode.NOT_FOUND, f"联系人 id={contact_id} 不存在", 404)
            data["contact_id"] = None
            skipped.append(f"联系人 #{contact_id}（已不存在或已被删除）")
        elif customer_id is None:
            customer_id = contact.customer_id
        elif contact.customer_id != customer_id:
            if strict:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"联系人 id={contact_id} 不属于客户 id={customer_id}",
                    422,
                )
            data["contact_id"] = None
            skipped.append(f"联系人 #{contact_id}（不属于同一个客户）")

    # 最终确定的客户再过一次范围校验：它可能是**推断**来的（来自单据或联系人），
    # 推断不该成为绕过数据范围的通道。
    if customer_id is not None and customer_id != data.get("customer_id"):
        final_customer = await session.get(Customer, customer_id)
        if final_customer is None or final_customer.deleted_at is not None:
            if strict:
                raise AppError(ErrorCode.NOT_FOUND, "关联客户不存在", 404)
            customer_id = None
            skipped.append("客户（由关联推断而来，已不存在）")
        else:
            try:
                await ensure_in_scope(session, user, owner_id=final_customer.owner_id,
                                      label="客户", allow_unowned=True)
            except AppError:
                if strict:
                    raise
                customer_id = None
                skipped.append("客户（由关联推断而来，不在你的数据范围内）")
    data["customer_id"] = customer_id

    return data, skipped


__all__ = ["normalize_task_refs"]
