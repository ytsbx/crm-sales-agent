"""应收与回款入参。"""

import math
from datetime import date
from decimal import Decimal

from pydantic import field_validator, BaseModel, Field

from app.core.patch_schema import PatchModel

from app.core import idempotency


class ReceivableCreate(BaseModel):
    """建应收节点。

    `order_id` 可选：`POST /orders/{id}/receivables` 从路径取，
    `POST /receivables` 从 body 取。两个入口共用这一份校验。
    """

    order_id: int | None = None
    plan_name: str = Field(min_length=1, max_length=64)
    due_date: date
    # 与 receivable_plans.amount Numeric(16,2) 对齐，超出两位小数直接拒绝，
    # 不让数据库静默舍入。
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    remark: str | None = None


class ReceivableUpdate(PatchModel):
    """改应收节点：只改传进来的字段。"""

    plan_name: str | None = Field(default=None, min_length=1, max_length=64)
    due_date: date | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)
    remark: str | None = None


class ReceivableGenerate(BaseModel):
    """按比例生成应收计划，例如 30% 定金 + 70% 尾款。"""

    ratios: list[float]
    """各期比例，例如 [0.3, 0.7]，合计必须是 1。

    ⚠️ `list[float]` **放得进 NaN / Infinity**（它们是合法 JSON 里合法写出来的
    字面值 `NaN` / `Infinity`），下面必须显式挡掉：
      - `abs(sum([nan]) - 1) > 0.0001` → **恒为 False**（任何与 NaN 的比较都是 False），
        于是"比例之和必须等于 1"这道校验被**绕过**；
      - 紧接着 `Decimal(str(nan))` = `Decimal('NaN')`，`any(r <= 0 ...)` 抛
        `decimal.InvalidOperation` → 冒成 500（C5-08，2026-10-10 修）。
    不在这里拦，就等于把一个 500 交给"看起来完全合法"的入参。
    """
    first_due_date: date
    second_due_date: date | None = None
    first_name: str = "定金"
    second_name: str = "尾款"
    #: 弱网重试的请求键（第七批 7.9）：双击"生成计划"带同一把键时，
    #: 第二次拿回第一次的结果，而不是撞"该订单已有应收计划"的报错。
    request_key: str | None = Field(default=None, max_length=idempotency.MAX_KEY_LENGTH)

    @field_validator("ratios")
    @classmethod
    def _ratios_must_be_finite(cls, value: list[float]) -> list[float]:
        """只收**有限数**（C5-08）。NaN/Infinity 一律当参数错误，不放它们往下走。"""
        bad = [v for v in value if not math.isfinite(v)]
        if bad:
            raise ValueError(
                f"每期比例必须是有限数（收到了 {', '.join(str(v) for v in bad)}）——"
                f"NaN / Infinity 不是合法比例"
            )
        return value


class PaymentCreate(BaseModel):
    receivable_plan_id: int | None = None
    received_date: date
    # 与 payment_records.received_amount Numeric(16,2) 对齐。
    received_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    payment_method: str | None = None
    voucher_note: str | None = None
    #: 币种（第七批 7.9）：不传就**继承应收节点的币种**，绝不落到列默认的 CNY ——
    #: 美元节点上的回款被记成人民币，汇总时按 1:1 相减，账错了还看不出来。
    #: 传了且与节点不一致 → 明确拒绝（跨币种核销口径未定，不猜汇率）。
    currency: str | None = Field(default=None, max_length=8)
    #: 弱网重试的请求键（第七批 7.9）：同一份表单反复提交带同一把键 → 只建一行；
    #: 不同的键各自建行，所以同额同日的两笔真实回款不会被合并。
    #: 也可以放 `X-Request-Key` 头里（见 core/idempotency.request_key_from）。
    request_key: str | None = Field(default=None, max_length=idempotency.MAX_KEY_LENGTH)


class PaymentUpdate(PatchModel):
    """改回款登记：金额/日期/方式/凭证说明。

    改金额必须重算应收节点状态，否则"收齐了"还显示部分回款。
    已确认/已驳回的回款不允许改（财务结论不能事后改数）。

    继承 `PatchModel`（N06，2026-10-09 修）：从前这里是 `BaseModel`，于是
    `received_amount` / `received_date` 这两个**库列非空**的字段显式传 `null`
    会一路走到数据库才撞非空约束，用户看到 **500「服务器内部错误」**——
    而它其实只是一句"金额不能为空"（实测两个字段都 500）。
    判据现场从 `payment_records` 的列定义读（见 `core/patch_schema.py`），
    不人工再登记一遍"哪些字段非空"。
    """

    received_date: date | None = None
    received_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=16, decimal_places=2
    )
    payment_method: str | None = None
    voucher_note: str | None = None
    #: 币种只在**与应收节点一致**时才接受；传 null 等于"按节点币种重新对齐"。
    currency: str | None = Field(default=None, max_length=8)


class PaymentAction(BaseModel):
    comment: str | None = None
