"""客户名下单据的「跟着负责人走」清单与批量改派。

## 为什么需要（2026-10-07，业务确认后的口径）

此前两条交接路的深度不一样：

- **离职交接**（`wecom/service.py` 的 `transfer_relations`）按类别逐项把商机、待办、
  打样（含生产责任）、报价单、订单草稿、订单，以及它们生成的业务文件，
  一起改到接手人名下；
- **日常改负责人**（`customer/service.py` 的 `transfer_customer`：人工转移、
  主管分配、批量转移、撞单裁定、公海指派/领取都走它）**只改客户本身和没办完的待办**，
  单据原地不动。

于是出现"客户转给了李四，但客户名下的报价、订单还写着张三"：李四打开这个客户，
订单/报价标签是空的（这些子资源接口按**单据自己的负责人**过滤数据范围），
而 AI 的「客户全貌」按"客户可见即资料可见"又把它们讲了出来 —— 两条路口径打架，
业务上也说不过去（新人不知道之前报过什么价、发过什么货，没法服务这个客户）。

现在统一的规则：

    客户的负责人从 A 换成 B 时，把原本挂在 A 名下、属于这个客户的单据一起改成 B。

## 两条边界

- **只搬 A 名下的**：别的在职同事负责的单子**不动** —— 与离职交接同一条判据
  （第六批审查第 4 条：把在职同事的单子一起改走，等于把别人的责任悄悄划走）；
- **"留名"的一个字不碰**：订单的业绩归属（`sales_owner_id`）、各表的历史创建人、
  跟进作者、报价批准人 —— 方案 §61 明确要求"交接后保留"。
  变的只是"现在由谁负责"，不是"这单算谁的"。

## 并发：条件必须跟着 UPDATE 一起下去

**不能"先按条件查 id、再按 id 更新"。** 本项目实测过（2026-10-07 复验）：
一个事务正把订单改派给 C（未提交、握着那一行的锁），此时客户从 A 转给 B ——
迁移先按 `owner_id == A` 读到旧值、再卡在行锁上；等 C 提交后继续执行，
`where id in (...)` 仍然命中，于是**把 C 刚接手的改派静默覆盖成了 B**。

所以改派走"带条件的 UPDATE + `RETURNING id`"：

- 条件保留在 UPDATE 里，PostgreSQL 在 READ COMMITTED 下**拿到锁会重新判定条件**，
  发现"负责人已经不是 A 了"就跳过这一行 —— 同事刚接手的单据就此留住
  （与"只搬原负责人名下的"是同一条边界，只是把它执行到并发场景）；
- `RETURNING` 拿的是**真的改了**的行：下游按来源追生成文件、回给调用方的条数，
  都必须是"实际改动"而不是"打算改动"。

## 与离职交接的关系

离职交接支持**逐项指定不同的接手人**（打样的跟单责任与生产责任可以交给两个人），
所以它仍走自己那套逐项搬运，只把本模块的 `DOCUMENT_KINDS` 当作共用口径 ——
`scripts/check_customer_handover_documents.py` 里有一条断言盯着两边别漂移。

## 待办为什么不在这里

待办的口径是"**只搬没办完的**"（已完成的待办属于历史记录，当时的处理人和完成时间
要留在档案里），与"单据整体跟着客户走"不同，所以它留在 `customer/service.py`
的 `transfer_customer` 里搬，本模块不掺和。
"""

from __future__ import annotations

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

#: 跟着客户负责人走的单据类别。
#: 与离职交接的类别名单（`wecom/model.py` 的 `TRANSFER_KIND_LABEL`）同一口径：
#: 商机、打样（跟单/生产两个责任字段）、报价单、订单草稿、销售订单。
DOCUMENT_KINDS: tuple[str, ...] = (
    "opportunity",
    "sample",
    "sample_production",
    "quote",
    "order_draft",
    "order",
)

#: 类别 → 人话（提示语里给操作者看）。
#: 与离职交接的 `wecom/model.TRANSFER_KIND_LABEL` **同一套说法**，但不直接
#: import（wecom 那边反过来依赖 customer，横向 import 会成环）；
#: `check_customer_handover_documents` 里有一条断言盯着两边别漂移。
DOCUMENT_KIND_LABEL: dict[str, str] = {
    "opportunity": "商机",
    "sample": "打样单（跟单）",
    "sample_production": "打样单（生产）",
    "quote": "报价单",
    "order_draft": "订单草稿",
    "order": "销售订单",
    "biz_doc": "生成文件",
}


def empty_result() -> dict:
    """什么都没搬时的统一结构。

    调用方（转移接口）不必判 `None`：结果里 `moved_total == 0` 且
    `skipped_total == 0` 就代表这次没有单据跟着走。
    """
    return {
        "moved": {},
        "skipped": {},
        "moved_total": 0,
        "skipped_total": 0,
        "skipped_labels": [],
    }


async def reassign_customer_documents(
    session: AsyncSession,
    *,
    customer_id: int,
    from_owner_id: int | None,
    to_owner_id: int | None,
) -> dict:
    """把客户名下、原本挂在 `from_owner_id` 名下的单据改成 `to_owner_id`。

    返回 `{"moved": {类别: 张数}, "skipped": {类别: 张数}, "moved_total",
    "skipped_total", "skipped_labels"}`（见 `empty_result`）。两个 id 有空、或两人
    相同时直接返回 `empty_result()`（没有可搬的）。

    `skipped` 是**并发下的如实交代**：读的时候这些单据还在原负责人名下，等真正
    写下去时已经被别人接走了 —— 按"只搬原负责人名下的"这条边界，它们留在
    接手人手里，并把张数报给操作者（主人口径 2026-10-07：要提示，不要静默）。

    **不加状态过滤**：这里搬的是"这个客户的历史归谁负责"，不是"接手人的工作量"。
    所以已结束的打样、已取消的订单、已软删的报价**一起搬** —— 客户易主之后
    它的档案应当是完整的（离职交接那边按 `status != cancelled` / `SAMPLE_OPEN_STATUSES`
    过滤，是因为它同时在生成"接手人要做哪些活"的清单，目的不同）。
    """

    if from_owner_id is None or to_owner_id is None or from_owner_id == to_owner_id:
        return empty_result()

    # 局部 import：这些模型分属各业务模块，放在模块顶部会让 customer 模块
    # 在导入期就拽上整条业务链（import 环的风险）。
    from app.modules.bizdoc.model import BizDoc
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import OrderDraft, SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest

    moved: dict[str, int] = {}
    skipped: dict[str, int] = {}

    async def move(kind: str, model, *conditions, **values) -> list[int]:
        """按条件把这批行改成新负责人；返回**实际改动的**行 id，并记账。

        返回的 id 供下游按"来源单据 id"去够生成文件（报价/订单/草稿/打样）。
        条数**按实际改动记**：`moved` 是真改了的，`skipped` 是"读的时候还在
        原负责人名下、改的时候已经不是了"—— 也就是期间被别人接走的那些。

        ⚠️ 条件必须**跟着 UPDATE 一起下去**，不能"先按条件查 id、再按 id 更新"：
        后者在并发下会静默覆盖别人的改动（见模块开头"并发"那一段的实际复现）。
        保留条件之后，PostgreSQL 在 READ COMMITTED 下拿到行锁会**重新判定条件**，
        发现"负责人已经不是原负责人了"就跳过 —— 同事刚接手的单据就此留住。

        上面那次"按条件先读一遍"**只用来数被接走了几张**，不参与正确性：
        正确性一律由带条件的 UPDATE 保证（少一次读也不影响结果，多这一次只是
        为了把"跳过了几张"如实报给操作者）。
        """
        candidates = set(
            (await session.execute(select(model.id).where(*conditions))).scalars().all()
        )
        result = await session.execute(
            update(model).where(*conditions).values(**values).returning(model.id)
        )
        moved_ids = list(result.scalars().all())
        taken_over = sorted(candidates - set(moved_ids))
        if moved_ids:
            moved[kind] = len(moved_ids)
        if taken_over:
            skipped[kind] = len(taken_over)
        return moved_ids

    await move(
        "opportunity",
        Opportunity,
        Opportunity.customer_id == customer_id,
        Opportunity.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )

    # 打样有两个责任字段，各自可能挂在原负责人名下，分开搬
    # （跟单责任决定列表/详情可见性，生产责任决定车间照谁的单做）
    #
    # **跟单责任的 id 要留着**：打样**生成**的对外单据（图纸、确认单…）跟着
    # **跟单责任**走 —— 与离职交接 `_reassign_generated_docs(field="sample_request_id")`
    # 同一口径。只改了生产责任的单子不算，否则会把不归跟单人管的文件也牵走。
    sample_ids = await move(
        "sample",
        SampleRequest,
        SampleRequest.customer_id == customer_id,
        SampleRequest.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )
    await move(
        "sample_production",
        SampleRequest,
        SampleRequest.customer_id == customer_id,
        SampleRequest.production_owner_id == from_owner_id,
        production_owner_id=to_owner_id,
    )

    quote_ids = await move(
        "quote",
        Quote,
        Quote.customer_id == customer_id,
        Quote.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )

    draft_ids = await move(
        "order_draft",
        OrderDraft,
        OrderDraft.customer_id == customer_id,
        OrderDraft.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )

    # 只动 `owner_id` —— `sales_owner_id`（业绩归属）**保持原样**，
    # "交接后保留历史业绩归属"是方案 §61 的硬要求。
    order_ids = await move(
        "order",
        SalesOrder,
        SalesOrder.customer_id == customer_id,
        SalesOrder.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )

    # 生成的对客文件（报价档案、下单文件、合同生成稿、打样需求单…）单独一张表。
    # 判据是并集：挂在这个客户名下 **或** 来源单据是这次**真的搬过**的那几张 ——
    # 后者是为了兜住 `customer_id` 为空的历史行。
    #
    # ⚠️ **打样这一支不能漏**：实测（2026-10-07 复验）历史打样文件的 `customer_id`
    # 为空、只关联了 `sample_request_id`；少了这条，打样单的跟单责任搬到 B 了、
    # 文件还挂在 A 名下，B 打开就被数据范围挡成 403。
    biz_conditions = [BizDoc.customer_id == customer_id]
    if sample_ids:
        biz_conditions.append(BizDoc.sample_request_id.in_(sample_ids))
    if order_ids:
        biz_conditions.append(BizDoc.order_id.in_(order_ids))
    if quote_ids:
        biz_conditions.append(BizDoc.quote_id.in_(quote_ids))
    if draft_ids:
        biz_conditions.append(BizDoc.order_draft_id.in_(draft_ids))
    await move(
        "biz_doc",
        BizDoc,
        BizDoc.owner_id == from_owner_id,
        or_(*biz_conditions),
        owner_id=to_owner_id,
    )

    return {
        "moved": moved,
        "skipped": skipped,
        "moved_total": sum(moved.values()),
        "skipped_total": sum(skipped.values()),
        "skipped_labels": [DOCUMENT_KIND_LABEL[kind] for kind in skipped],
    }
