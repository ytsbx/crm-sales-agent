"""应收与回款入参。"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

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
    remark: str | None = Field(default=None, max_length=255)
    request_key: str | None = Field(default=None, max_length=idempotency.MAX_KEY_LENGTH)


class ReceivableUpdate(PatchModel):
    """改应收节点：只改传进来的字段。"""

    plan_name: str | None = Field(default=None, min_length=1, max_length=64)
    due_date: date | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)
    remark: str | None = Field(default=None, max_length=255)


class PaymentAllocationItem(BaseModel):
    """一条分配：这笔回款分给哪一期、多少。"""

    plan_id: int
    # 与 `payment_allocations.amount` 同精度（Numeric(16,2)）。
    # `gt=0`：要清掉某期的分配请**不传这一期**（整份替换式提交），
    # 传 0 会让"这条分配到底存不存在"变得含糊。
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)


class PaymentAllocationUpdate(BaseModel):
    """人工调整一笔回款的分配（第十批主人 2026-10-13 定的口径）。

    主人原话（选方案1）："系统不能擅自调整已经登记的收款归属，必须由有权限的
    人员确认后才能调整" + "调整时要保留完整的操作审计记录"。

    所以这个接口是**整份替换**：传进来的就是这笔回款的全部分配。
    没传的期 = 不分配。这样"原归属 → 新归属"在审计里是一条清晰的对照，
    而不是一串增删改。
    """

    allocations: list[PaymentAllocationItem] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _no_duplicate_plans(self):
        seen = set()
        for item in self.allocations:
            if item.plan_id in seen:
                raise ValueError(f"同一期只能有一条分配（期 id={item.plan_id} 传了两次）")
            seen.add(item.plan_id)
        return self


def _ratios_must_be_finite(value: list[Decimal]) -> list[Decimal]:
    # B03-15 决策1 顺带：比例换精确 Decimal（pydantic 对 float 走 str 最短往返、
    # 对字符串逐位解析），16 位边界不再经 float 丢分；NaN/Infinity 仍显式挡掉。
    bad = [ratio for ratio in value if not ratio.is_finite()]
    if bad:
        raise ValueError(
            f"每期比例必须是有限数（收到了 {', '.join(str(ratio) for ratio in bad)}）——"
            "NaN / Infinity 不是合法比例"
        )
    return value


FiniteReceivableRatios = Annotated[list[Decimal], AfterValidator(_ratios_must_be_finite)]


class ReceivableGenerate(BaseModel):
    """按比例生成应收计划，例如 30% 定金 + 70% 尾款。"""

    ratios: FiniteReceivableRatios
    """各期比例，例如 [0.3, 0.7]，合计必须是 1。

    ⚠️ B03-15 决策1 起比例为精确 `Decimal`（字符串逐位解析、float 走 str 最短往返）。
    NaN / Infinity 仍须显式挡掉（JSON 里它们是合法字面值）：
      - 与 NaN 的任何比较恒为 False，"比例之和必须等于 1" 会被**绕过**；
      - 紧接着下游 `any(r <= 0 ...)` 抛 `decimal.InvalidOperation` → 冒成 500
        （C5-08，2026-10-10 修）。校验器用 `is_finite()` 显式拒绝。
    不在这里拦，就等于把一个 500 交给"看起来完全合法"的入参。
    """
    first_due_date: date
    second_due_date: date | None = None
    #: ⚠️ 每期名称与库列 `receivable_plans.plan_name` 一致：`String(64)`。
    #: 少了这个上限，前端传 65 字会撞数据库 → **500**（第九批问题 ⑦）。
    first_name: str = Field(default="定金", max_length=64)
    second_name: str = Field(default="尾款", max_length=64)
    #: **整份计划一次提交**（第九批问题 ⑥）：给全了就不用"先生成、再逐条 PATCH"。
    #: 与 `ratios` 按下标对齐。三期及以上必须提供整份到期日，名称省略时按期号生成。
    due_dates: list[date] | None = None
    names: list[str] | None = None

    @field_validator("due_dates", mode="before")
    @classmethod
    def _require_date_entries(cls, value):
        if isinstance(value, list):
            for index, due in enumerate(value, start=1):
                if due is None or (isinstance(due, str) and not due.strip()):
                    raise ValueError(f"第 {index} 期缺少到期日")
        return value

    @model_validator(mode="after")
    def _full_plan_lengths(self):
        periods = len(self.ratios)
        if self.due_dates is None and periods >= 3:
            raise ValueError("三期及以上的应收计划必须提供每一期的到期日")
        if self.due_dates is not None:
            if len(self.due_dates) < periods:
                raise ValueError(f"从第 {len(self.due_dates) + 1} 期起缺少到期日，请补齐后再生成")
            if len(self.due_dates) > periods:
                raise ValueError("到期日的个数不能多于应收期数")
        if self.names is not None:
            if len(self.names) != periods:
                raise ValueError("每期都要有一个名称（names 与 ratios 个数要一致）")
            for name in self.names:
                if not str(name).strip():
                    raise ValueError("每期名称不能为空")
                if len(str(name)) > 64:
                    raise ValueError("每期名称最多 64 个字（与数据库列一致）")
        return self
    #: 弱网重试的请求键（第七批 7.9）：双击"生成计划"带同一把键时，
    #: 第二次拿回第一次的结果，而不是撞"该订单已有应收计划"的报错。
    request_key: str | None = Field(default=None, max_length=idempotency.MAX_KEY_LENGTH)


class PaymentCreate(BaseModel):
    receivable_plan_id: int | None = None
    received_date: date
    # 与 payment_records.received_amount Numeric(16,2) 对齐。
    received_amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    payment_method: str | None = Field(default=None, max_length=32)
    voucher_note: str | None = Field(default=None, max_length=255)
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
    payment_method: str | None = Field(default=None, max_length=32)
    voucher_note: str | None = Field(default=None, max_length=255)
    #: 币种只在**与应收节点一致**时才接受；传 null 等于"按节点币种重新对齐"。
    currency: str | None = Field(default=None, max_length=8)


class PaymentAction(BaseModel):
    comment: str | None = None


class PaymentVoidRequest(BaseModel):
    """财务作废已确认回款。

    与「驳回」不同：驳回只发生在待确认；作废发生在钱已经确认入账之后，
    是纠错动作，必须留下原因。原因会写进审计并显示在订单的回款记录上。
    """

    reason: str = Field(min_length=2, max_length=200)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("请填写作废原因（至少 2 个字）")
        return value
