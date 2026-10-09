from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class QuoteCreate(BaseModel):
    opportunity_id: int | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    valid_until: date | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    # 外贸口径（可选）：不填就是内贸，全人民币
    currency: str = "CNY"
    exchange_rate: Decimal | None = None
    """不填则自动取汇率表里该币种的当前汇率并落快照（02-ER §11）。"""
    #: 请求幂等键（第八批 8.15）：弱网/超时后的重试带同一把键，服务端只建一条报价。
    #: 不给也不报错（老客户端照常工作），但响应里会说明这次没有幂等保护。
    #: **同一把键只允许对应同一份内容**：改完表单再提交请用新键（前端换表单时重新生成）。
    request_key: str | None = Field(default=None, max_length=128)


class QuoteVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    valid_until: date | None = None


class QuoteItemInput(PatchModel):
    """报价明细入参。两条路径（文档场景09）：

    继承 `PatchModel` 是为了拿到项目统一的「必填字段不许传 null / 长度上限」
    守卫（判据现场从列定义读，见 `core/patch_schema.py`）—— 与 `QuoteItemUpdate`
    同一套尺子。**行为不变**：调用方是整份替换，本来就不依赖 `exclude_unset`。

    - **现货**：给 `sku_id`，`quoted_price` 留空则只采用已维护的客户价/指导价；
      没有有效售价时必须手工填写，不能把成本试算当成正式报价；
    - **定制**：尚无正式 SKU 时给 `inquiry_id` + 人工核价的 `unit_cost` 与
      `quoted_price`。定制项必须给成本——不给成本就只能按 0 算，
      会得出 100% 毛利、低价审批也不会触发（与 A06「无成本不造假」同口径）。
    """

    sku_id: int | None = None
    #: 定制需求 id（与 sku_id 至少给一个）
    inquiry_id: int | None = None
    #: 定制项展示名，落快照；不填用需求标题
    item_name: str | None = None
    #: 定制项人工核价成本（元/件，不含运费）
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    #: 数量与单价**必须跟库列对齐**（N01，2026-10-09 修）：
    #: `quote_items.quantity` 是 `Numeric(16,3)`、`quoted_price` 是 `Numeric(16,4)`。
    #: 从前这两个字段**一个约束都没有**，于是三件事都能发生：
    #:   ① 数量填 `-1` 能保存，版本合计变成负数；
    #:   ② 数量填 `0` 能保存；
    #:   ③ 小数超过三位时**库静默四舍五入**（填 1.23456 → 存 1.235），
    #:      而版本合计是拿**未舍入的原值**算的（185.18），明细金额又是拿
    #:      **落库后的值**算的（185.25）—— 同一张单两个数，差 0.07。
    #: 写法照抄项目里同一类字段的既有做法（`OpportunityItemCreate.quantity`、
    #: `OrderDraftLine.quantity`），不另创一套。
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=16, decimal_places=3)
    quoted_price: Decimal | None = Field(
        default=None, gt=0, max_digits=16, decimal_places=4
    )
    opportunity_item_id: int | None = None
    spec_snapshot: str | None = None
    #: 0 是合法值（明确零运费），所以是 `ge=0` 不是 `gt=0`
    logistics_cost: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    remark: str | None = None


class QuoteItemUpdate(PatchModel):
    """改一条报价明细：只改传进来的字段（`exclude_unset` 语义）。

    数值约束与新增**同一套尺子**（N01）：新增拦住、编辑放行就是一条旁路 ——
    编号规则那轮已经吃过这个教训（同一条业务规则两个入口两个答案）。
    """

    model_config = ConfigDict(extra="ignore")

    quantity: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=3)
    quoted_price: Decimal | None = Field(
        default=None, gt=0, max_digits=16, decimal_places=4
    )
    #: 定制行的核价成本（人民币）。现货行的成本来自成本表，传了也不生效。
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    logistics_cost: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=4)
    remark: str | None = None


class QuoteChargeInput(BaseModel):
    """新增一条附加费用（含运费）。

    金额口径（与 `QuoteChargeUpdate` 同一条尺子，**前端校验之外后端必须再校验一遍**）：
    非负、有限、最多两位小数。运费就是 `charge_type="logistics"` 的这条费用
    （2026-10-09：不另建一套运费金额，避免两套金额都被加进总额）。

    通过本接口填进来的物流费用视为**业务已确认的具体金额** ——
    "明确确认的零运费"（`amount=0`）也是合法输入，落库时打上确认时刻，
    与"压根没填"区分开（见 `QuoteCharge.logistics_confirmed_at`）。

    ⚠️ **`amount` 必填**（2026-10-09 审查实测后修）：从前它写着
    `default=Decimal(0)`，于是"漏传金额"与"明确填 0"在**入参层就变成同一个值**，
    上面那句"与压根没填区分开"根本没有实现。实测漏传金额的运费请求：
    返回成功、落库 `amount=0`、**还写上了确认时刻**，随后正式发送也放行 ——
    空金额被当成了"已确认的零运费"，绕过"运费必须确认具体金额"。

    现在：漏传 → 参数错误（400，点名字段），**端点体不执行，不落费用行也不写确认时刻**；
    显式 `amount: 0` 仍然合法并照样打确认时刻 —— "明确的零运费"依然走得通。
    """

    charge_type: str = "other"
    description: str | None = None
    #: 必填：漏传 = 参数错误。`ge=0` 允许"明确确认的零运费"。
    amount: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    is_discount: bool = False


class QuoteChargeUpdate(PatchModel):
    """改一条附加费用（03-API §22）。只传要改的字段。

    金额校验与新增同一条尺子（非负、有限、两位小数）。**改了物流费用的金额，
    确认时刻会重新打一次** —— 改的是"已确认的实际运费"这个事实本身，
    拿改动前的确认时刻去背书改动后的金额是错的。
    """

    model_config = ConfigDict(extra="ignore")

    charge_type: str | None = None
    description: str | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
    is_discount: bool | None = None
    sort_no: int | None = None


class ApprovalAction(BaseModel):
    comment: str | None = None


class QuoteUpdate(BaseModel):
    """改报价单本身（03-API §20）。

    注意：**不能改金额、明细与备注** —— 那些都属于版本
    （`Quote` 表本身只有 contact_id / owner_id / valid_until 这几个可变字段，
    备注在 `QuoteVersion.remark` 上）。改版本的备注要走
    `PATCH /quote-versions/{id}`，否则会出现"单据改了但版本快照没变"。
    """

    model_config = ConfigDict(extra="ignore")

    contact_id: int | None = None
    owner_id: int | None = None
    valid_until: date | None = None


class QuoteClone(BaseModel):
    """复制报价单（03-API §20）。

    典型场景：同款产品给另一家客户报价、或客户要求"照上次再来一单"。
    复制出的报价是**草稿**，版本内容照抄但状态全部重置 ——
    审批通过/已发送是上一单的结论，不能继承。
    """

    model_config = ConfigDict(extra="ignore")

    customer_id: int | None = None
    opportunity_id: int | None = None
    contact_id: int | None = None
    owner_id: int | None = None
    valid_until: date | None = None
    copy_items: bool = True
    remark: str | None = None


class QuoteExpire(BaseModel):
    """把报价标记为已失效（03-API §21）。

    状态机里一直有 `expired` 但**没有任何地方会写它** ——
    过了有效期没人处理，看板上永远停在"已发送"。这里补上入口。
    """

    model_config = ConfigDict(extra="ignore")

    reason: str | None = None


class SendRequest(BaseModel):
    channel: str = Field(default="邮件", min_length=1, max_length=32)
    receiver: str | None = Field(default=None, max_length=128)
    # 同一次人工确认重试沿用；再次实际发送生成新 key，不把重试当重发。
    request_key: UUID | None = None


class SubmitApprovalRequest(BaseModel):
    reason: str | None = None


class DeclinedRequest(BaseModel):
    reason: str | None = None


__all__ = ["datetime", "Field"]
