"""物流试算入参（PRD §14、03-API §19）。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LogisticsCalculateRequest(BaseModel):
    """试算输入：SKU / 数量 / 包装 / 重量 / 体积 / 起运地 / 目的地 / 运输方式。"""

    model_config = ConfigDict(extra="ignore")

    sku_id: int
    quantity: Decimal = Decimal(1)
    origin: str | None = None
    """起运地。"""
    destination: str | None = None
    """目的地。"""
    shipping_method: str | None = None
    """运输方式（陆运/快递/空运…）。不填则匹配到的费率全部返回。"""
    package_type: str | None = None
    """包装方式。仅作记录与回显——分包装的体积/重量差异靠下面的 override 表达。"""
    weight_override: Decimal | None = None
    """单件重量（kg），覆盖 SKU 上的值。"""
    volume_override: Decimal | None = None
    """单件体积（m³），覆盖 SKU 上的值。"""

    customer_id: int | None = None
    opportunity_id: int | None = None
    save: bool = False
    """true 时把选中的方案落成一条 logistics_quotes 记录。"""
    selected_provider: str | None = None
    """save=true 时指定选哪家；不填则取费用最低的方案。"""


class LogisticsCompareRequest(BaseModel):
    """多方案对比：同一批输入，列出所有匹配费率并排序。"""

    model_config = ConfigDict(extra="ignore")

    sku_id: int
    quantity: Decimal = Decimal(1)
    origin: str | None = None
    destination: str | None = None
    shipping_method: str | None = None
    weight_override: Decimal | None = None
    volume_override: Decimal | None = None


# 费率的新增/修改模型定义在 pricing/schema.py（费率维护属于价格中心），
# 这里不重复定义，避免两处字段漂移。

