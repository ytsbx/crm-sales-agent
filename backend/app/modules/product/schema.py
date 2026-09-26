"""产品与 SKU 的入参结构。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_line: str | None = None
    category: str | None = None
    brand: str | None = None
    description: str | None = None
    knowledge: str | None = None


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    product_line: str | None = None
    category: str | None = None
    brand: str | None = None
    description: str | None = None
    knowledge: str | None = None
    status: str | None = None


class SkuCreate(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64)
    name: str | None = None
    specification: str | None = None
    color: str | None = None
    material: str | None = None
    length: Decimal | None = None
    width: Decimal | None = None
    height: Decimal | None = None
    weight: Decimal | None = None
    carton_qty: int | None = None
    carton_volume: Decimal | None = None
    moq: int | None = None
    package_type: str | None = None
    unit: str | None = "件"


class SkuUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku_code: str | None = None
    name: str | None = None
    specification: str | None = None
    color: str | None = None
    material: str | None = None
    length: Decimal | None = None
    width: Decimal | None = None
    height: Decimal | None = None
    weight: Decimal | None = None
    carton_qty: int | None = None
    carton_volume: Decimal | None = None
    moq: int | None = None
    package_type: str | None = None
    unit: str | None = None
    status: str | None = None


class SkuStandaloneCreate(SkuCreate):
    """扁平路径新增 SKU（03-API §15 `POST /skus`）。

    嵌套路径（`/products/{id}/skus`）的产品 id 在 URL 上，扁平路径没有，
    所以这里必填。
    """

    product_id: int
