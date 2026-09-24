from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    task_type: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    lead_id: int | None = None
    opportunity_id: int | None = None
    quote_id: int | None = None
    order_id: int | None = None
    owner_id: int | None = None
    priority: str = "normal"
    due_at: datetime | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    priority: str | None = None
    status: str | None = None
    due_at: datetime | None = None
    owner_id: int | None = None


class TaskComplete(BaseModel):
    completion_note: str | None = None
