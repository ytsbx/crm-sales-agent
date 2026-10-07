"""第八批 8.2：完整联系方式的可见范围（口径经用户 2026-10-06 确认）。

已确认的规则：**负责人看本客户、主管看本团队、管理员全部；其他可见人员脱敏，
完整信息需另授权**（授权码 `customer:contact_full`）。

这一套守三件事：

1. 脱敏本身是"保留可辨识部分"（`138****8000`），不是打成一团星号——
   否则用户分不清"没填"和"没权限看"，会反复去找管理员要权限；
2. **能看到客户 ≠ 能看到完整电话**：公海客户对所有有查看权限的人可见，
   但它的联系人电话必须脱敏（这正是"另授权"存在的意义）；
3. 授权后/本人/主管确实拿得到完整值——脱敏不能矫枉过正到业务做不了。
"""

from datetime import UTC, datetime

import pytest

from tests.conftest import SyncSessionAsAsync, make_user

from app.modules.contact_util import (
    CONTACT_FULL_PERMISSION,
    full_contact_customer_ids,
    mask_contact_value,
    mask_contact_fields,
)
from app.modules.customer.model import Contact, Customer
from app.modules.customer.router import list_contacts

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ 脱敏格式


def test_mask_phone_keeps_head_and_tail():
    assert mask_contact_value("13812348000", "phone") == "138****8000"
    # 带分隔符的长号仍按"前 3 后 4"处理，分隔符先剔掉再数
    assert mask_contact_value("021-88889999", "phone") == "021****9999"


def test_mask_short_phone_never_reveals_everything():
    """短号 / 座机也要**真的挡住数字**（审查 2026-10-07 实测的漏洞）。

    原来固定写 `前3 + **** + 后4`：7 位号码正好 3+4=7，中间四个星号一个数字都没挡
    （`1234567` 原样可还原）；5、6 位还会把同一批数字重复显示。
    现在**按数字个数**分档，硬判据是"至少挡住两位数字"。
    """
    cases = {
        "12": "**",  # 2 位：全遮
        "123": "1**",
        "1234": "1***",
        "12345": "12**5",  # 5 位：留前 2 后 1
        "123456": "12***6",
        "1234567": "12****7",  # 7 位：改前会整串露出
        "12345678": "123**678",  # 8 位：留前 3 后 3
        "1234567890": "123****890",
        "13812348000": "138****8000",  # 11 位手机号形状保持不变
    }
    for raw, expected in cases.items():
        masked = mask_contact_value(raw, "phone")
        assert masked == expected, f"{raw} → {masked}，期望 {expected}"
        hidden = sum(1 for a, b in zip(raw, masked) if a != b)
        assert hidden >= 2, f"{raw} 脱敏后只挡住 {hidden} 个字符，等于没脱敏"


def test_mask_ignores_separators_when_counting_digits():
    """分隔符不能替数字"被遮住"（审查 2026-10-07 复验的第二种写法）。

    `"123    4567"` 有 11 个**字符**但只有 7 个**数字**：按字符长度分档会把它
    归进"11 位以上"那一档，于是 `前3 + **** + 后4` 的四个星号正好压在空格上，
    七个数字一位都没挡住。**判据只能是数字的个数。**

    验收要求（审查原文）：带空格短号、带横线短号、区号、括号、国际号码及分机格式，
    未授权返回值都不能完整还原原号码。
    """
    cases = {
        "123    4567": "12****7",
        "123 - 4567": "12****7",
        "12 34567": "12****7",
        "(123)4567": "12****7",
        "021-88889999": "021****9999",
        "(021) 8888 9999": "021****9999",
        "+86 138 1234 8000": "861****8000",
        "010-8888-6666-801": "010****6801",  # 带分机的座机
    }
    for raw, expected in cases.items():
        masked = mask_contact_value(raw, "phone")
        assert masked == expected, f"{raw} → {masked}，期望 {expected}"
        original = "".join(ch for ch in raw if ch.isdigit())
        visible = "".join(ch for ch in masked if ch.isdigit())
        assert len(visible) < len(original), f"{raw} 的数字一个都没藏住（{masked}）"


def test_mask_email_keeps_first_char_and_domain():
    assert mask_contact_value("zhang@example.com", "email") == "z***@example.com"


def test_mask_keeps_blank_as_blank():
    """没填就是没填：返回空值本身，不能被脱敏成一个"看起来填了"的串。"""
    assert mask_contact_value(None, "phone") is None
    assert mask_contact_value("", "phone") == ""
    assert mask_contact_value("   ", "phone") == "   "


def test_mask_fields_marks_the_payload():
    payload = mask_contact_fields(
        {"name": "张伟", "mobile": "13812348000", "email": "z@a.com", "wechat": "zw12345"}
    )
    assert payload["contact_masked"] is True
    assert payload["name"] == "张伟"          # 姓名不脱敏
    assert payload["mobile"] == "138****8000"
    assert payload["email"] == "z***@a.com"
    assert payload["wechat"] == "zw***"


# ------------------------------------------------------- 可见范围（谁算"自己人"）


def _seed(session, *, owner_id: int | None) -> tuple[Customer, Contact]:
    customer = Customer(
        name="宏远包装",
        level="A",
        status="active",
        pool_status="public" if owner_id is None else "private",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    contact = Contact(
        customer_id=customer.id,
        name="张伟",
        mobile="13812348000",
        email="zhang@example.com",
        wechat="zhangwei001",
        is_primary=True,
        created_at=NOW,
    )
    session.add(contact)
    session.flush()
    return customer, contact


@pytest.mark.anyio
async def test_owner_sees_full_contact(db_session):
    customer, contact = _seed(db_session, owner_id=101)
    user = make_user(101, data_scope="self")
    session = SyncSessionAsAsync(db_session)
    result = await list_contacts(customer_id=customer.id, user=user, session=session)
    assert result["data"][0]["mobile"] == "13812348000"
    assert "contact_masked" not in result["data"][0]


@pytest.mark.anyio
async def test_public_pool_customer_contacts_are_masked(db_session):
    """公海客户人人都能看 —— 但完整电话不是"能看客户"就送的。"""
    customer, contact = _seed(db_session, owner_id=None)
    user = make_user(101, data_scope="self")
    session = SyncSessionAsAsync(db_session)
    result = await list_contacts(customer_id=customer.id, user=user, session=session)
    payload = result["data"][0]
    assert payload["contact_masked"] is True
    assert payload["mobile"] == "138****8000"
    assert payload["email"] == "z***@example.com"


@pytest.mark.anyio
async def test_team_manager_sees_full_contact(db_session):
    """主管看本团队：数据范围是 department 时，团队成员的客户对他就是完整的。"""
    from app.modules.user.model import Department, User

    dept = Department(id=9, name="销售部", created_at=NOW, updated_at=NOW)
    db_session.add(dept)
    db_session.add(
        User(
            id=101, name="业务员", username="sales", password_hash="x",
            department_id=9, status="active", created_at=NOW, updated_at=NOW,
        )
    )
    db_session.add(
        User(
            id=201, name="主管", username="manager", password_hash="x",
            department_id=9, status="active", created_at=NOW, updated_at=NOW,
        )
    )
    db_session.flush()
    customer, _contact = _seed(db_session, owner_id=101)

    manager = make_user(201, roles=("manager",), data_scope="department")
    manager.department_id = 9
    session = SyncSessionAsAsync(db_session)
    allowed = await full_contact_customer_ids(session, manager, {customer.id})
    assert allowed == {customer.id}


@pytest.mark.anyio
async def test_admin_and_explicit_grant_are_unrestricted(db_session):
    customer, _contact = _seed(db_session, owner_id=101)
    session = SyncSessionAsAsync(db_session)

    admin = make_user(999, roles=("admin",), data_scope="all")
    assert await full_contact_customer_ids(session, admin, {customer.id}) is None

    granted = make_user(998, roles=("salesperson",), data_scope="self")
    granted.permissions = {CONTACT_FULL_PERMISSION}
    assert await full_contact_customer_ids(session, granted, {customer.id}) is None

    other = make_user(997, roles=("salesperson",), data_scope="self")
    assert await full_contact_customer_ids(session, other, {customer.id}) == set()
