from decimal import Decimal

from pydantic import BaseModel, Field


class SessionIn(BaseModel):
    title: str | None = None
    context_type: str | None = None
    context_id: int | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ActionReject(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------- 专用分析入参
# 03-API §37 Specialized。每个都要求明确的业务对象 id ——
# "分析一下"没有对象就只能瞎编，宁可直接 400。

class CustomerSummaryIn(BaseModel):
    customer_id: int


class OpportunityAnalysisIn(BaseModel):
    opportunity_id: int


class ProductRecommendationIn(BaseModel):
    customer_id: int | None = None
    opportunity_id: int | None = None
    limit: int = Field(default=5, ge=1, le=20)


class PricingAnalysisIn(BaseModel):
    sku_id: int
    quantity: Decimal = Field(default=Decimal(1), gt=0)
    customer_id: int | None = None


class QuoteDraftIn(BaseModel):
    opportunity_id: int
    customer_id: int | None = None


class FollowupSuggestionIn(BaseModel):
    customer_id: int | None = None
    lead_id: int | None = None


class RiskAnalysisIn(BaseModel):
    order_id: int | None = None
    """不给就分析当前用户数据范围内的全部应收。"""
