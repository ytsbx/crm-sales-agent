"""客户与联系人的入参结构。"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.patch_schema import PatchModel



class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="客户名称")
    short_name: str | None = None
    customer_type: str | None = "企业"
    country: str | None = "中国"
    region: str | None = None
    address: str | None = None
    domain: str | None = None
    tax_no: str | None = None
    source: str | None = None
    level: str | None = None
    owner_id: int | None = None
    remark: str | None = None
    #: 请求幂等键（第八批 8.15）：弱网/超时后的重试带同一把键，服务端只建一条。
    #: 不给也不报错（老客户端照常工作），但响应里会说明这次没有幂等保护 ——
    #: 不能让人以为"重试一定安全"。
    request_key: str | None = Field(default=None, max_length=128)


class CustomerUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    short_name: str | None = None
    customer_type: str | None = None
    country: str | None = None
    region: str | None = None
    address: str | None = None
    domain: str | None = None
    tax_no: str | None = None
    source: str | None = None
    level: str | None = None
    status: str | None = None
    remark: str | None = None
    next_followup_at: datetime | None = None
    #: 最近有效联系时间。给这个字段的用途是**补核历史客户**（第七批 7.5）：
    #: 导入时没提供联系日期的老客户被标成"联系时间未知"，不参与自动回收；
    #: 业务核对出真实时间后填这里，标记随之清掉、重新回到回收视野。
    last_followup_at: datetime | None = None


class CustomerTransfer(BaseModel):
    owner_id: int | None = None
    reason: str | None = None


class CustomerRestore(BaseModel):
    """恢复一个被**直接删除**的客户（03-API §42.2）。

    为什么带一个可选的新负责人：**原负责人可能已经停用**（账号也可能没了）。
    那种情况下把客户恢复出来，等于制造一条"挂在停用账号下"的脏数据 ——
    所以服务层会拒绝并要一个人选，由调用方在这里一并指定。
    不传就沿用原负责人（删除时**没有**清空过它）。
    """

    model_config = ConfigDict(extra="ignore")

    owner_id: int | None = None


class PoolRelease(BaseModel):
    """人工把客户放进公海。

    `reason` 不只是留痕：客户**还在履约中**（有在途订单/未结应收/有效正式报价/
    在途打样）时，普通释放会被拦；填了原因表示主管**明确要求例外释放**，
    这时才放行，并把保护事项与原因一起写进审计（返工单 6.3）。
    """

    reason: str | None = None


class CustomerExportPurpose(str, Enum):
    CUSTOMER_FOLLOW_UP = "customer_follow_up"
    BUSINESS_ANALYSIS = "business_analysis"
    MANAGEMENT_REPORT = "management_report"
    DATA_RECONCILIATION = "data_reconciliation"
    HISTORICAL_MIGRATION = "historical_migration"
    OTHER = "other"


class CustomerExportFilter(BaseModel):
    """导出筛选条件，与列表页参数保持一致。"""

    purpose: CustomerExportPurpose
    purpose_note: str | None = Field(default=None, max_length=200)
    keyword: str | None = None
    level: str | None = None
    status: str | None = None
    source: str | None = None
    owner_id: int | None = None
    pool_status: str | None = None

    @model_validator(mode="after")
    def validate_purpose_note(self):
        note = (self.purpose_note or "").strip()
        if self.purpose == CustomerExportPurpose.OTHER and not note:
            raise ValueError("用途选择“其他”时，补充说明必填")
        if self.purpose != CustomerExportPurpose.OTHER and note:
            raise ValueError("仅用途选择“其他”时填写补充说明")
        self.purpose_note = note or None
        return self


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    title: str | None = None
    department: str | None = None
    mobile: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    is_primary: bool = False
    remark: str | None = None


class ContactUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    title: str | None = None
    department: str | None = None
    mobile: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    is_primary: bool | None = None
    remark: str | None = None


class ContactStandaloneCreate(ContactCreate):
    """扁平路径创建联系人（03-API §8 `POST /contacts`）。

    嵌套路径（`/customers/{id}/contacts`）的客户 id 在 URL 上，
    扁平路径没有，所以这里必填。
    """

    customer_id: int


class ContactBindCustomer(BaseModel):
    """把联系人绑定 / 改挂到客户（03-API §8）。"""

    customer_id: int
    is_primary: bool = False
