"""案例接口请求体。"""

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    customer_id: int | None = None
    customer_label: str | None = Field(None, max_length=128)
    industry: str | None = Field(None, max_length=64)
    product_line: str | None = Field(None, max_length=64)
    stage_reached: str | None = Field(None, max_length=32)
    problem_tags: list[str] = Field(default_factory=list)
    background: str | None = None
    goal: str | None = None
    key_actions: str | None = None
    objection_handling: str | None = None
    process: str | None = None
    result: str | None = None
    lessons: str | None = None
    quote_id: int | None = None
    order_id: int | None = None
    sample_id: int | None = None
    opportunity_id: int | None = None


class CaseUpdate(BaseModel):
    """作者在 draft/rejected 状态下修改；字段全可选，传了才改。"""

    title: str | None = Field(None, max_length=200)
    customer_id: int | None = None
    customer_label: str | None = Field(None, max_length=128)
    industry: str | None = Field(None, max_length=64)
    product_line: str | None = Field(None, max_length=64)
    stage_reached: str | None = Field(None, max_length=32)
    problem_tags: list[str] | None = None
    background: str | None = None
    goal: str | None = None
    key_actions: str | None = None
    objection_handling: str | None = None
    process: str | None = None
    result: str | None = None
    lessons: str | None = None
    quote_id: int | None = None
    order_id: int | None = None
    sample_id: int | None = None
    opportunity_id: int | None = None


class CaseReview(BaseModel):
    approve: bool
    note: str | None = Field(None, max_length=255)
