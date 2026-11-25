"""数量 PATCH 的空值语义及用户可见错误，不连接数据库或外部服务。"""

from decimal import Decimal

from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.core.errors import register_exception_handlers
from app.modules.opportunity.schema import OpportunityCreate, OpportunityItemCreate, OpportunityItemUpdate
from app.modules.opportunity.model import Opportunity, OpportunityItem


def test_null_quantity_is_a_clear_parameter_error():
    assert OpportunityItem.__table__.c.quantity.nullable is False
    app = FastAPI(default_response_class=DecimalJSONResponse)
    register_exception_handlers(app)

    @app.patch("/item")
    def update(payload: OpportunityItemUpdate):
        return payload.model_dump(exclude_unset=True, mode="json")

    client = TestClient(app)
    response = client.patch("/item", json={"quantity": None})
    assert response.status_code == 400
    assert response.json()["code"] == 40001
    assert "数量不能为空" in response.json()["message"]
    assert client.patch("/item", json={"target_price": None}).json() == {"target_price": None}
    assert client.patch("/item", json={"remark": "只改备注"}).json() == {"remark": "只改备注"}


def test_valid_decimal_values_are_preserved():
    item = OpportunityItemUpdate(quantity="2.125", target_price="1.2345")
    assert item.quantity == Decimal("2.125")
    assert item.target_price == Decimal("1.2345")


@pytest.mark.parametrize("schema,model,payload,field", [
    (OpportunityCreate, Opportunity, {"customer_id": 1, "title": "项目"}, "source"),
    (OpportunityCreate, Opportunity, {"customer_id": 1, "title": "项目"}, "competitor"),
    (OpportunityCreate, Opportunity, {"customer_id": 1, "title": "项目"}, "risk_level"),
    (OpportunityCreate, Opportunity, {"customer_id": 1, "title": "项目"}, "next_action"),
    (OpportunityItemCreate, OpportunityItem, {"sku_id": 1}, "specification"),
    (OpportunityItemCreate, OpportunityItem, {"sku_id": 1}, "color"),
    (OpportunityItemCreate, OpportunityItem, {"sku_id": 1}, "package_requirement"),
    (OpportunityItemCreate, OpportunityItem, {"sku_id": 1}, "destination"),
])
def test_new_opportunity_text_fits_its_column(schema, model, payload, field):
    limit = model.__table__.c[field].type.length
    at_limit = "甲" * limit
    assert getattr(schema(**payload, **{field: at_limit}), field) == at_limit
    with pytest.raises(ValidationError, match=f"最长 {limit} 个字符"):
        schema(**payload, **{field: at_limit + "甲"})
