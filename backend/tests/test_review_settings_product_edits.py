from decimal import Decimal

import pytest
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError, ErrorCode
from app.modules.product import router as product_router
from app.modules.product.model import Product, Sku, SkuFieldAuthority
from app.modules.product.schema import ProductUpdate, SkuUpdate
from app.modules.user import router as user_router
from app.modules.user.model import Permission, Role, role_permissions
from app.modules.user.schema import RoleCreate
from conftest import SyncSessionAsAsync, make_user


def create_tables(session, *tables):
    Base.metadata.create_all(session.get_bind(), tables=tables)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["product", "sku"])
async def test_product_edits_merge_unrelated_fields_and_reject_same_field(db_session, kind):
    create_tables(db_session, Product.__table__, Sku.__table__, SkuFieldAuthority.__table__, AuditLog.__table__)
    product = Product(name="原产品名", brand="原品牌")
    db_session.add(product)
    db_session.flush()
    sku = Sku(product_id=product.id, sku_code="SKU-EDIT", name="原 SKU 名", weight=Decimal("1.250"))
    db_session.add(sku)
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})
    first = make_user(101, permissions={"product:manage"})
    second = make_user(102, permissions={"product:manage"})

    if kind == "product":
        update, schema, row, field, old, new = (
            product_router.update_product, ProductUpdate, product, "brand", "原品牌", "新品牌",
        )
    else:
        update, schema, row, field, old, new = (
            product_router.update_sku, SkuUpdate, sku, "weight", "1.25", "2.375",
        )

    original_name = row.name
    await update(row.id, schema(**{field: new, "expected_fields": {field: old}}), request, first, session)
    await update(row.id, schema(name="新名称", expected_fields={"name": original_name}), request, second, session)
    assert row.name == "新名称"
    assert getattr(row, field) == (new if kind == "product" else Decimal(new))

    with pytest.raises(AppError) as error:
        await update(
            row.id, schema(name="另一名字", expected_fields={"name": original_name}), request, first, session,
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    assert error.value.http_status == 409
    await session.rollback()
    assert row.name == "新名称"
    assert db_session.query(AuditLog).count() == 2

    await update(row.id, schema(name="核对后修改", expected_fields={"name": "新名称"}), request, first, session)
    assert row.name == "核对后修改"


@pytest.mark.anyio
async def test_role_create_respects_selected_status_and_defaults_to_active(db_session):
    create_tables(db_session, Role.__table__, Permission.__table__, role_permissions, AuditLog.__table__)
    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": []})
    user = make_user(101, permissions={"user:manage"})
    disabled = await user_router.create_role(
        RoleCreate(code="disabled_test", name="停用测试角色", status="disabled"), request, user, session,
    )
    default = await user_router.create_role(
        RoleCreate(code="default_test", name="默认测试角色"), request, user, session,
    )
    assert disabled["data"]["status"] == "disabled"
    assert default["data"]["status"] == "active"
