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


async def reassign_customer_documents(
    session: AsyncSession,
    *,
    customer_id: int,
    from_owner_id: int | None,
    to_owner_id: int | None,
) -> dict[str, int]:
    """把客户名下、原本挂在 `from_owner_id` 名下的单据改成 `to_owner_id`。

    返回 `{类别: 改了几条}`，供调用方留痕与断言用。任何一个 id 为空、或两人相同
    时直接返回空字典（没有可搬的）。

    **不加状态过滤**：这里搬的是"这个客户的历史归谁负责"，不是"接手人的工作量"。
    所以已结束的打样、已取消的订单、已软删的报价**一起搬** —— 客户易主之后
    它的档案应当是完整的（离职交接那边按 `status != cancelled` / `SAMPLE_OPEN_STATUSES`
    过滤，是因为它同时在生成"接手人要做哪些活"的清单，目的不同）。
    """

    if from_owner_id is None or to_owner_id is None or from_owner_id == to_owner_id:
        return {}

    # 局部 import：这些模型分属各业务模块，放在模块顶部会让 customer 模块
    # 在导入期就拽上整条业务链（import 环的风险）。
    from app.modules.bizdoc.model import BizDoc
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import OrderDraft, SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest

    moved: dict[str, int] = {}

    async def move(model, *conditions, **values) -> list[int]:
        """按条件把这批行改成新负责人，返回被改动的行 id。

        先取 id 再按 id 更新（而不是直接用原条件 update）：生成文件那张表要按
        "来源单据 id" 追，得先把 id 拿在手里。数据量是"一个客户的单据"，
        多一次查询无所谓。
        """
        ids = list(
            (await session.execute(select(model.id).where(*conditions))).scalars().all()
        )
        if ids:
            await session.execute(update(model).where(model.id.in_(ids)).values(**values))
        return ids

    moved["opportunity"] = len(
        await move(
            Opportunity,
            Opportunity.customer_id == customer_id,
            Opportunity.owner_id == from_owner_id,
            owner_id=to_owner_id,
        )
    )

    # 打样有两个责任字段，各自可能挂在原负责人名下，分开搬
    # （跟单责任决定列表/详情可见性，生产责任决定车间照谁的单做）
    moved["sample"] = len(
        await move(
            SampleRequest,
            SampleRequest.customer_id == customer_id,
            SampleRequest.owner_id == from_owner_id,
            owner_id=to_owner_id,
        )
    )
    moved["sample_production"] = len(
        await move(
            SampleRequest,
            SampleRequest.customer_id == customer_id,
            SampleRequest.production_owner_id == from_owner_id,
            production_owner_id=to_owner_id,
        )
    )

    quote_ids = await move(
        Quote,
        Quote.customer_id == customer_id,
        Quote.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )
    moved["quote"] = len(quote_ids)

    draft_ids = await move(
        OrderDraft,
        OrderDraft.customer_id == customer_id,
        OrderDraft.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )
    moved["order_draft"] = len(draft_ids)

    # 只动 `owner_id` —— `sales_owner_id`（业绩归属）**保持原样**，
    # "交接后保留历史业绩归属"是方案 §61 的硬要求。
    order_ids = await move(
        SalesOrder,
        SalesOrder.customer_id == customer_id,
        SalesOrder.owner_id == from_owner_id,
        owner_id=to_owner_id,
    )
    moved["order"] = len(order_ids)

    # 生成的对客文件（报价档案、下单文件、合同生成稿…）单独一张表。
    # 判据两条并集：挂在这个客户名下 **或** 来源单据是这次搬过的那几张 ——
    # 后者是为了兜住 `customer_id` 为空的历史行。
    biz_conditions = [BizDoc.customer_id == customer_id]
    if order_ids:
        biz_conditions.append(BizDoc.order_id.in_(order_ids))
    if quote_ids:
        biz_conditions.append(BizDoc.quote_id.in_(quote_ids))
    if draft_ids:
        biz_conditions.append(BizDoc.order_draft_id.in_(draft_ids))
    moved["biz_doc"] = len(
        await move(
            BizDoc,
            BizDoc.owner_id == from_owner_id,
            or_(*biz_conditions),
            owner_id=to_owner_id,
        )
    )

    return {kind: count for kind, count in moved.items() if count}
