"""未分级在客户档案中保持 NULL/空串；UNRATED 仅用于筛选与规则选择。"""
from sqlalchemy import false, func, or_

UNRATED = "UNRATED"


def level_filter(column, levels):
    """单个/多个等级规则统一覆盖历史 NULL 和空串。"""
    if isinstance(levels, str):
        levels = [levels]
    rated = [level for level in levels if level != UNRATED]
    clauses = [column.in_(rated)] if rated else []
    if UNRATED in levels:
        clauses.append(func.trim(func.coalesce(column, "")) == "")
    return or_(*clauses) if clauses else false()


def level_label(level):
    return "未分级" if not level or level == UNRATED else f"{level} 级"


def level_code(level):
    return (level or "").strip() or UNRATED
