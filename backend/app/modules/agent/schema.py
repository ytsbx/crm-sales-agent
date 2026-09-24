from pydantic import BaseModel, Field


class SessionIn(BaseModel):
    title: str | None = None
    context_type: str | None = None
    context_id: int | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ActionReject(BaseModel):
    reason: str | None = None
