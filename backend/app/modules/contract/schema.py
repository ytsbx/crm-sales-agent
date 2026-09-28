"""合同模板与文档接口的请求体。"""

from datetime import date

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    """新增模板=新增版本：同名同类型自动 version+1，旧版本保留。"""

    doc_type: str = "contract"  # contract / monthly
    name: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1)
    enabled: bool = True


class DocumentCreate(BaseModel):
    """从模板生成合同草稿。extra_fields 是"空白的地方业务员自己填"的部分。"""

    template_id: int
    customer_id: int
    order_id: int | None = None
    quote_id: int | None = None
    title: str | None = None
    extra_fields: dict[str, str] = Field(default_factory=dict)
    expiry_date: date | None = None
    parent_id: int | None = None


class DocumentSign(BaseModel):
    """登记签署：file_id 来自通用上传接口（/files/upload），签的是扫描件。"""

    file_id: int
    note: str | None = None


class DocumentVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
