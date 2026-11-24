"""案例接口请求体。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core.patch_schema import PatchModel



class CaseEvidenceItem(BaseModel):
    """案例的一条证据单据（§3.7：一个案例可以有多张单据支撑）。

    `kind` 取值见后端 `cases/model.CASE_EVIDENCE_KINDS`
    （`quote` / `order` / `sample` / `opportunity`）。
    """

    model_config = ConfigDict(extra="ignore")

    kind: str
    business_id: int
    label: str | None = Field(None, max_length=128)
    note: str | None = Field(None, max_length=255)


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    customer_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    customer_label: str | None = Field(None, max_length=128)
    industry: str | None = Field(None, max_length=64)
    product_line: str | None = Field(None, max_length=64)
    stage_reached: str | None = Field(None, max_length=32)
    problem_tags: list[str] = Field(default_factory=list, max_length=20)
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
    #: 证据单据列表（多条）。传了就以它为准，旧的四列自动同步成每类的第一条。
    evidences: list[CaseEvidenceItem] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("案例标题不能为空")
        return value

    @field_validator("problem_tags")
    @classmethod
    def tags_clean(cls, value):
        return _clean_tags(value)


def _clean_tags(value: list[str] | None) -> list[str] | None:
    """B18-09：标签去空白、拒空项、单项≤32 字（数量上限由 Field max_length 管）。"""
    if value is None:
        return value
    cleaned = []
    for tag in value:
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("问题标签不能为空")
        tag = tag.strip()
        if len(tag) > 32:
            raise ValueError("单个问题标签最长 32 个字符")
        cleaned.append(tag)
    return cleaned


class CaseUpdate(PatchModel):
    """作者在 draft/rejected 状态下修改；字段全可选，传了才改。"""

    title: str | None = Field(None, max_length=200)
    customer_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    customer_label: str | None = Field(None, max_length=128)
    industry: str | None = Field(None, max_length=64)
    product_line: str | None = Field(None, max_length=64)
    stage_reached: str | None = Field(None, max_length=32)
    problem_tags: list[str] | None = Field(default=None, max_length=20)
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
    #: 证据单据列表（多条）。**传了就整体替换**（不是追加）——
    #: 界面提交的是"这一版挂了哪几张单"，逐条增删反而容易漏。
    evidences: list[CaseEvidenceItem] | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if value is None or not value.strip():
            raise ValueError("案例标题不能为空")
        return value.strip()

    @field_validator("problem_tags")
    @classmethod
    def tags_clean(cls, value):
        return _clean_tags(value)


class CaseReview(BaseModel):
    approve: bool
    note: str | None = Field(None, max_length=255)
