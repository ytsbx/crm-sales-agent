"""产品与 SKU 的入参结构。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_line: str | None = None
    category: str | None = None
    brand: str | None = None
    description: str | None = None
    knowledge: str | None = None


class ProductUpdate(PatchModel):
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
    # 这几个是**物理量**，负数没有意义，而且会一路算进运费（第十批 10.6 顺带修：
    # 试算侧挡住了手填的负数，但如果 SKU 上存着负重量，照样算出负结果）。
    # 用 `ge=0`（只禁负数）：0 在现有代码里等价于"没填"，语义不变。
    length: Decimal | None = Field(default=None, ge=0)
    width: Decimal | None = Field(default=None, ge=0)
    height: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, ge=0)
    carton_qty: int | None = Field(default=None, ge=0)
    carton_volume: Decimal | None = Field(default=None, ge=0)
    moq: int | None = None
    package_type: str | None = None
    unit: str | None = "件"


class SkuUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    sku_code: str | None = None
    name: str | None = None
    specification: str | None = None
    color: str | None = None
    material: str | None = None
    # 同 SkuCreate：物理量不许为负（要清空请传 null，不要传 0/负数）
    length: Decimal | None = Field(default=None, ge=0)
    width: Decimal | None = Field(default=None, ge=0)
    height: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, ge=0)
    carton_qty: int | None = Field(default=None, ge=0)
    carton_volume: Decimal | None = Field(default=None, ge=0)
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


# ---------------------------------------------------------------- §8.14 主数据权威


class SkuIngestRequest(BaseModel):
    """接收一条**外部来源**的 SKU 数据（简道云 / 聚水潭 / 企业桥接）。

    真实取数还没接通（交接说明 §0.3 第 5 条），所以这个入口由**桥接方**喂数据；
    它只登记"来源值 + 差异"，**不会**改写本地 SKU。
    """

    system_type: str = Field(min_length=1, max_length=32)
    external_code: str = Field(min_length=1, max_length=64)
    external_name: str | None = Field(default=None, max_length=200)
    shop_id: str | None = Field(default=None, max_length=64)
    source_updated_at: str | None = None
    #: 只接受 `master.MASTER_FIELDS` 里的字段名；其余字段名一律 422（不静默丢弃）。
    fields: dict[str, object] = Field(default_factory=dict)


class SkuRenameRequest(BaseModel):
    """来源改码。老身份行保留成历史，老编码仍能反查到同一个 SKU。"""

    new_external_code: str = Field(min_length=1, max_length=64)


class SkuStopRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class SkuAuthorityRequest(BaseModel):
    """登记某个字段的权威归属。`authority` 留空 = 撤回归属（未拍板）。"""

    sku_id: int
    field_name: str = Field(min_length=1, max_length=64)
    authority: str | None = Field(default=None, max_length=32)


class SkuMasterDiffConfirmRequest(BaseModel):
    """核定一条 SKU 主数据差异。

    `resolution` 由后端按差异类型白名单校验：同名不同码只允许"保留本地/人工裁定"，
    改码与停用只允许各自的专用结论。
    """

    resolution: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=1000)
