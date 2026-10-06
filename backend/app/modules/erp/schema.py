from datetime import date

from pydantic import BaseModel, Field


class OrderPushRequest(BaseModel):
    """按 CRM 订单号推送。"""

    order_no: str | None = None


class ReconcileRequest(BaseModel):
    """人工在外部系统核对到单号之后的登记请求。

    只收外部单号，不收"是否成功"这类结论：能否算已同步由后端按外部凭据判定，
    不接受调用方自报（自报成功正是"没有凭据也标已同步"的老问题）。
    """

    external_id: str = Field(min_length=1, max_length=128)


class StatusWebhookRequest(BaseModel):
    """对方系统推送的状态变更。

    用 CRM 订单号或外部单号定位订单，两者给一个即可；**两个都给时必须同源一致**，
    否则进异常队列、不改任何一个订单（§8.11）。
    status 是**对方系统的口径**，由 Adapter 的映射表翻译成 CRM 状态。
    """

    order_no: str | None = None
    erp_order_id: str | None = None
    status: str
    remark: str | None = None


# ---------------------------------------------------------------- §8.13 采集与对账


class CollectRequest(BaseModel):
    """触发一次只读采集。

    `object_type` 只允许 order / shipment / aftersale：这是这一轮真正落地采集骨架
    的三类。**不代表能采到**：适配器没有通过真实验收时，接口会如实回 422 +
    未接通状态，不会返回一个"成功但空"的结果。
    """

    object_type: str = Field(min_length=1, max_length=32)
    #: 店铺标识；留空 = 不分店铺（`*`）。两店同编号靠它隔离，不能省。
    shop_id: str | None = Field(default=None, max_length=64)
    page_size: int = Field(default=50, ge=1, le=200)
    max_pages: int = Field(default=200, ge=1, le=1000)


class SourceVerifyRequest(BaseModel):
    """登记某个来源的核实/授权结论。

    `verified=True` 时**必须给证据**：拿不到真实签名/端点/字段权威资料的这一轮，
    没有依据的"已核实"等于默认某个系统是权威（§8.14 明确禁止）。
    """

    system_type: str = Field(min_length=1, max_length=32)
    shop_id: str | None = Field(default=None, max_length=64)
    source_kind: str = Field(min_length=1, max_length=32)
    verified: bool = False
    evidence: str | None = None
    authorization_note: str | None = None


class MappingMatchRequest(BaseModel):
    """人工把一条待匹配的外部对象指到本地对象上。"""

    internal_id: int
    note: str | None = Field(default=None, max_length=500)


class MappingReplayRequest(BaseModel):
    """重放待匹配行（本地对象补齐后不必重拉外部数据）。"""

    system_type: str | None = Field(default=None, max_length=32)
    shop_id: str | None = Field(default=None, max_length=64)
    object_type: str | None = Field(default=None, max_length=32)
    limit: int = Field(default=500, ge=1, le=5000)


class ReconcileRunRequest(BaseModel):
    """触发一次期间对账。期间是**左闭右开** `[period_start, period_end)`。"""

    period_start: date
    period_end: date
    shop_id: str | None = Field(default=None, max_length=64)


class DiffConfirmRequest(BaseModel):
    """核定一条差异。

    `resolution` 的允许取值由后端按差异类型白名单校验（`reconcile.ALLOWED_RESOLUTIONS`）：
    不是所有差异都能选"以外部为准"，外部售后事实更是只允许"人工处理 + 写清依据"。
    """

    resolution: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=1000)
