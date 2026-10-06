"""第八批 8.2：全局搜索的模块权限、删除过滤与联系方式脱敏。

守五件事（每件都是修前会 FAIL 的真缺陷）：

1. **六类查询不检查模块权限**：零权限用户照样把六类业务查一遍。
   修后无权组不查、不返回计数/摘要（`denied=True` + 空 items）。
2. **联系人 join 漏了 `Customer.deleted_at`**：已删客户的联系人仍能被搜出来。
3. **空白关键词 trim 后变全库通配**：`"   "` 被 `strip()` 成 `%%`，等于把库里
   前 N 条全捞出来（按类型各捞一遍）。
4. **完整手机号/邮箱直接躺在响应里**：修后搜索结果里不再出现完整值——
   连"用什么电话能搜到"都不该被返回行为暴露。
5. **不同团队不可见**：有权限也不能跨范围搜到别人的对象。

用例用**真函数 + 内存 SQLite**（见 tests/conftest.py 的说明）：
`global_search` 是 FastAPI 路由函数，直接调用即可，依赖注入的 user/session
由用例显式传入——这比走 HTTP 更接近缺陷复现（审查方就是这么复现的）。
"""

from datetime import UTC, datetime

import pytest

from tests.conftest import SyncSessionAsAsync, make_user

from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.search.router import global_search

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _groups(data):
    return {group["type"]: group for group in data["groups"]}


def _seed(session, *, owner_id: int = 101, keyword: str = "宏远"):
    """一套「本人可见」的数据：客户 + 联系人 + 线索 + 商机 + 报价 + 订单。

    每类都用同一个关键词命中，这样"某一组被权限挡掉"与
    "某一组查不到数据"不会混为一谈。
    """
    customer = Customer(
        name=f"{keyword}科技",
        short_name=keyword,
        region="浙江",
        level="A",
        status="active",
        pool_status="private",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()

    contact = Contact(
        customer_id=customer.id,
        name=f"{keyword}张工",
        mobile="13800001111",
        email="zhang@hongyuan.example",
        is_primary=True,
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    stage = OpportunityStage(
        code=f"initial-{owner_id}",
        name="初步接洽",
        sequence=1,
        is_win=False,
        is_loss=False,
        status="active",
    )
    session.add_all([contact, stage])
    session.flush()

    opportunity = Opportunity(
        customer_id=customer.id,
        title=f"{keyword}产线改造",
        stage_id=stage.id,
        expected_amount=10000,
        status="open",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    quote = Quote(
        quote_no=f"Q-{keyword}-001",
        customer_id=customer.id,
        owner_id=owner_id,
        status="draft",
        created_at=NOW,
        updated_at=NOW,
    )
    order = SalesOrder(
        order_no=f"SO-{keyword}-001",
        customer_id=customer.id,
        owner_id=owner_id,
        total_amount=20000,
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    lead = Lead(
        name=f"{keyword}线索",
        company_name=f"{keyword}集团",
        mobile="13900002222",
        status="pending",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all([opportunity, quote, order, lead])
    session.commit()
    return customer, contact


async def _search(session, user, keyword: str, limit: int = 5):
    result = await global_search(
        keyword=keyword, limit=limit, user=user, session=SyncSessionAsAsync(session)
    )
    return result["data"]


@pytest.mark.anyio
async def test_zero_permission_user_runs_no_module_query(db_session):
    """零模块权限：六组全部无权，一组都不查、也不返回任何条目。"""
    _seed(db_session)
    user = make_user(101, permissions=set(), roles=["salesperson"], data_scope="self")
    data = await _search(db_session, user, "宏远")

    groups = _groups(data)
    assert set(groups) == {"customer", "opportunity", "quote", "order", "lead", "contact"}
    assert all(group["denied"] is True for group in groups.values())
    assert data["total"] == 0
    assert all(group["items"] == [] for group in groups.values())


@pytest.mark.anyio
async def test_each_group_requires_its_own_module_permission(db_session):
    """逐模块授权：只给 customer:view 时，其余四组必须仍是无权状态。

    这是 8.2 的核心口径——「能看客户」不等于「能看报价号」。
    """
    _seed(db_session)
    user = make_user(
        101, permissions={"customer:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await _search(db_session, user, "宏远")
    groups = _groups(data)

    assert groups["customer"]["denied"] is False and len(groups["customer"]["items"]) == 1
    assert groups["contact"]["denied"] is False and len(groups["contact"]["items"]) == 1
    for name in ("opportunity", "quote", "order", "lead"):
        assert groups[name]["denied"] is True, name
        assert groups[name]["items"] == [], name


@pytest.mark.anyio
async def test_granted_module_sees_only_own_scope(db_session):
    """有权限 + 不同团队：只看得到自己范围内的，别人的一条都不出现。"""
    _seed(db_session, owner_id=999, keyword="宏远")
    _seed(db_session, owner_id=101, keyword="宏远自有")
    user = make_user(
        101,
        permissions={
            "customer:view",
            "opportunity:view",
            "quote:view",
            "order:view",
            "lead:view",
        },
        roles=["salesperson"],
        data_scope="self",
    )
    data = await _search(db_session, user, "宏远")
    groups = _groups(data)

    for name, group in groups.items():
        assert group["denied"] is False, name
        titles = " ".join(str(item["title"]) for item in group["items"])
        assert "自有" in titles, name
    assert all(item["title"] != "宏远科技" for item in groups["customer"]["items"])


@pytest.mark.anyio
async def test_deleted_customer_hides_its_contacts(db_session):
    """已删客户的联系人不得出现——原实现只挡了 Contact.deleted_at。"""
    customer, _contact = _seed(db_session)
    customer.deleted_at = NOW
    db_session.commit()
    user = make_user(101, permissions={"customer:view"}, roles=["salesperson"], data_scope="self")
    data = await _search(db_session, user, "宏远")
    assert _groups(data)["contact"]["items"] == []

    # 对照：联系人自己没删、客户没删时必须搜得到，否则就是把功能改坏了
    customer.deleted_at = None
    db_session.commit()
    data = await _search(db_session, user, "宏远")
    assert len(_groups(data)["contact"]["items"]) == 1


@pytest.mark.anyio
async def test_blank_keyword_returns_empty_without_wildcard(db_session):
    """空白关键词直接空结果：不能 strip 成 `%%` 把全库捞出来。"""
    _seed(db_session)
    user = make_user(
        101,
        permissions={"customer:view", "lead:view"},
        roles=["salesperson"],
        data_scope="all",
    )
    for keyword in ("   ", "\t", ""):
        data = await _search(db_session, user, keyword)
        assert data["total"] == 0, repr(keyword)
        assert all(group["items"] == [] for group in data["groups"]), repr(keyword)


@pytest.mark.anyio
async def test_contact_response_masks_only_for_people_without_the_right(db_session):
    """搜索必须与其它入口**共用同一条**联系方式可见规则（用户 2026-10-06 确认）：
    负责人本客户 / 主管本团队 / 管理员全部 / 其他可见人员脱敏。

    所以这里守两边：
      · 本人负责的客户 → 给完整值（否则业务员在搜索里查不到自己的客户电话）；
      · 公海客户（无负责人，人人都能看见）→ 一律脱敏 —— 这正是"另授权"的意义，
        也正是"通过返回行为枚举完整电话"要封的那条路。
    """
    from app.modules.customer.model import Contact, Customer

    _seed(db_session)
    user = make_user(101, permissions={"customer:view"}, roles=["salesperson"], data_scope="self")

    # 本人负责的客户：完整值
    data = await _search(db_session, user, "宏远")
    own = _groups(data)["contact"]["items"][0]
    assert own["mobile"] == "13800001111"
    assert own["email"] == "zhang@hongyuan.example"
    assert own["contact_masked"] is False

    # 公海客户：同一入口、同一用户，但联系方式必须脱敏
    public = Customer(
        name="宏远公海客户",
        level="B",
        status="active",
        pool_status="public",
        owner_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(public)
    db_session.flush()
    db_session.add(
        Contact(
            customer_id=public.id,
            name="李四",
            mobile="13900002222",
            email="li@hongyuan.example",
            is_primary=True,
            created_at=NOW,
        )
    )
    db_session.commit()

    data_public = await _search(db_session, user, "李四")
    masked = _groups(data_public)["contact"]["items"][0]
    blob = str(masked)
    assert "13900002222" not in blob
    assert "li@hongyuan.example" not in blob
    assert masked["mobile"] == "139****2222"
    assert masked["email"] == "l***@hongyuan.example"
    assert masked["contact_masked"] is True
