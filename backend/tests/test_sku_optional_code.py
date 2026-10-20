from types import SimpleNamespace
import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import make_engine, make_user, SyncSessionAsAsync
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.product import router, service
from app.modules.product.model import Product, Sku
from app.modules.product.schema import SkuCreate, SkuStandaloneCreate, SkuUpdate

USER = make_user(1, permissions=("product:manage",), data_scope="all")
REQUEST = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


@pytest.fixture
def sku_db():
    engine = make_engine()
    for model in (Product, Sku, AuditLog):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Product(id=1, name="周转箱")); raw.commit()
        yield raw, SyncSessionAsAsync(raw)
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["nested", "standalone"])
@pytest.mark.parametrize("code", [None, "", "   ", "omitted"])
async def test_create_without_company_code(sku_db, entry, code):
    raw, session = sku_db
    data = {"name": "周转箱蓝色", "specification": "600×400"}
    if code != "omitted": data["sku_code"] = code
    if entry == "nested":
        result = await router.create_sku(1, SkuCreate(**data), REQUEST, USER, session)
    else:
        result = await router.create_standalone_sku(SkuStandaloneCreate(product_id=1, **data), REQUEST, USER, session)
    stored = raw.get(Sku, result["data"]["id"])
    assert stored.sku_code.strip() and len(stored.sku_code) <= 64
    assert stored.name == "周转箱蓝色" and stored.specification == "600×400"
    assert raw.query(AuditLog).filter_by(business_type="sku", action="create").count() == 1


@pytest.mark.anyio
async def test_manual_code_unchanged_and_duplicate_still_rejected(sku_db):
    raw, session = sku_db
    await router.create_sku(1, SkuCreate(sku_code="COMPANY-001"), REQUEST, USER, session)
    with pytest.raises(AppError, match="已存在"):
        await router.create_sku(1, SkuCreate(sku_code="COMPANY-001"), REQUEST, USER, session)
    assert raw.query(Sku).one().sku_code == "COMPANY-001"


def test_edit_does_not_allow_erasing_existing_code():
    with pytest.raises(ValueError): SkuUpdate(sku_code="")
    with pytest.raises(ValueError): SkuUpdate(sku_code=None)


@pytest.mark.anyio
async def test_auto_code_skips_existing_and_deleted_codes(sku_db, monkeypatch):
    raw, session = sku_db
    from datetime import UTC, datetime
    raw.add(Sku(product_id=1, sku_code="SKU-" + "A" * 32, deleted_at=datetime.now(UTC)))
    raw.commit()
    values = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(service, "uuid4", lambda: SimpleNamespace(hex=next(values)))
    code = await service.resolve_new_sku_code(session, None)
    assert code == "SKU-" + "B" * 32
    assert raw.query(Sku).one().sku_code == "SKU-" + "A" * 32


@pytest.mark.anyio
async def test_edit_other_fields_keeps_existing_company_code(sku_db):
    raw, session = sku_db
    result = await router.create_sku(1, SkuCreate(sku_code="OLD", name="原名称"), REQUEST, USER, session)
    await router.update_sku(result["data"]["id"], SkuUpdate(name="新名称"), REQUEST, USER, session)
    stored = raw.query(Sku).one()
    assert stored.sku_code == "OLD" and stored.name == "新名称"


def test_auto_code_does_not_bypass_existing_field_validation():
    for data in ({"name": "x" * 201}, {"specification": "x" * 201}, {"weight": -1}, {"sku_code": "x" * 65}):
        with pytest.raises(ValueError): SkuCreate(**data)
