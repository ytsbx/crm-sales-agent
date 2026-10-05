"""合同模板与文档接口的请求体。"""

from datetime import date

from pydantic import BaseModel, Field, field_validator


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
    # 合同钉死的报价**版本**。报价可以出 V2/V3，只记报价单号无法证明金额依据的是哪一版。
    # 不传也能生成（提前备合同），但不传就没有金额——登记签署前必须补上正式依据。
    # 只传 order_id 时，后端会按订单依据的那一版自动带出来。
    quote_version_id: int | None = None
    title: str | None = None
    extra_fields: dict[str, str] = Field(default_factory=dict)
    expiry_date: date | None = None
    # 协议生效日。续签时必填（业务口径 2026-10-05）：只记到期日处理不了
    # 「提前签、未来才生效」——那种情况下旧协议还得继续适用一段。
    effective_date: date | None = None
    parent_id: int | None = None
    # 续签时**是否替代旧协议**：勾了才结束旧协议的在办提醒。
    # 默认不勾——提前续签、旧协议还在适用期是很常见的情况，
    # 一登记就掐掉旧提醒会让还在生效的协议没人管。
    supersede_parent: bool = False
    # 幂等键：前端打开生成弹窗时生成一个，重复提交 / 网络重试带同一个值。
    # 不传也能用，但那样重复提交就防不住——所以前端必须带。
    request_key: str | None = Field(default=None, max_length=64)


class DocumentSign(BaseModel):
    """登记签署：file_id 来自通用上传接口（/files/upload），签的是扫描件。"""

    file_id: int
    note: str | None = None


class DocumentVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        # `min_length=1` 挡不住 "   " —— 三个空格也算"填了"。作废原因是要写进台账和
        # 审计、给后人看的东西，纯空白跟不填一样没法追溯，等同默认值。
        text = value.strip()
        if not text:
            raise ValueError("请填写作废原因")
        return text
