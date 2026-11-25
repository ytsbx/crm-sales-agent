"""Private CRM drafts retain their original account and business references."""

from sqlalchemy import case, false, func, or_, select, true

from app.core.errors import AppError, ErrorCode


def private_draft(model):
    table = model.__tablename__
    if table == "quotes":
        from app.modules.quote.model import QuoteVersion

        submitted = select(QuoteVersion.id).where(
            QuoteVersion.id == model.current_version_id,
            or_(QuoteVersion.approval_status.in_(("pending", "approved")), QuoteVersion.sent_at.is_not(None)),
        ).correlate(model).exists()
        return (model.status == "draft") & ~submitted
    if table == "order_drafts":
        return model.status == "draft"
    if table == "quote_versions":
        return (model.approval_status == "not_submitted") & model.sent_at.is_(None)
    if table == "biz_docs":
        return model.status == "draft"
    if table == "sales_cases":
        return model.status.in_(("draft", "rejected"))
    if table == "sample_requests":
        oa = model.source_context["dingtalk_oa"]
        return (model.status == "pending") & oa["mode"].as_string().is_not(None) & (
            ~func.coalesce(oa["submitted"].as_boolean(), false())
        )
    if table in {"contract_documents", "custom_inquiries"}:
        from app.modules.dingtalk.model import OaInstance

        oa_type = "contract" if table == "contract_documents" else "inquiry"
        submitted = select(OaInstance.id).where(
            OaInstance.oa_type == oa_type, OaInstance.inquiry_id == model.id,
            OaInstance.instance_id.is_not(None),
        )
        submitted = submitted.correlate(model)
        if oa_type == "inquiry":
            submitted = submitted.where(OaInstance.inquiry_version == model.version)
            return model.extra["oa_draft"].as_string().is_not(None) & ~submitted.exists()
        return (model.status == "draft") & ~submitted.exists()
    raise ValueError(f"Draft privacy is not defined for {table}")


def draft_creator(model):
    if model.__tablename__ == "sales_cases":
        return model.author_id
    if model.__tablename__ == "sample_requests":
        handover_owner = model.source_context["handover_owner_id"].as_integer()
        return case((handover_owner == model.owner_id, model.owner_id),
                    else_=func.coalesce(model.created_by, model.owner_id))
    if model.__tablename__ in {"quotes", "order_drafts", "biz_docs"}:
        return func.coalesce(model.created_by, model.owner_id)
    return model.created_by


def draft_scope(model, user_id, normal_scope=None):
    private = private_draft(model)
    return or_(private & (draft_creator(model) == user_id), ~private & (
        true() if normal_scope is None else normal_scope
    ))


async def ensure_draft_access(session, model, row_id, user_id):
    """Return whether this visible record is a private draft owned by the caller."""
    result = (await session.execute(select(private_draft(model), draft_creator(model)).where(model.id == row_id))).first()
    if result and result[0]:
        if result[1] != user_id:
            raise AppError(ErrorCode.NOT_FOUND, "记录不存在", 404)
        return True
    return False
