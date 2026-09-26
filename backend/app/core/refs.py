"""引用存在性校验。

背景：`leads` / `followups` / `tasks` 这些表在数据库层**没有外键约束**
（见 `alembic` 迁移与 `02-ER` 设计：这些是"弱关联"，允许关联对象日后被删）。
没有约束就意味着：传一个不存在的 `customer_id` / `owner_id` 会被静默接受，
库里留下悬空引用 —— 列表页显示空白、统计口径悄悄错、权限判定拿到 None。

所以在写接口里显式校验。`quotes` / `sample_requests` 等有真实 FK 的表也要走这里，
否则会撞 FK 约束报 500（而不是可读的 40401）。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode


async def ensure_refs(
    session: AsyncSession,
    *,
    model: Any,
    ids: dict[str, int | None],
    label: str,
    soft_delete: bool = True,
    raise_on_missing: bool = True,
) -> list[tuple[str, int]]:
    """校验 `{字段名: id}` 里的每个 id 都存在。

    `soft_delete=True` 时，`deleted_at` 非空视为不存在。
    不存在的字段直接跳过（None 表示"不关联"，是合法值）。

    `raise_on_missing=False` 时不抛错，把缺失项以 `[(字段名, id)]` 返回 ——
    自动化校验脚本要"一次跑完所有用例看全部结果"，用这个开关。
    返回值为空列表表示全部存在。
    """
    missing: list[tuple[str, int]] = []
    for field, value in ids.items():
        if value is None:
            continue
        row = await session.get(model, value)
        if row is None or (soft_delete and getattr(row, "deleted_at", None) is not None):
            missing.append((field, value))
    if missing and raise_on_missing:
        field, value = missing[0]
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"{label} id={value} 不存在（字段 {field}）",
            404,
        )
    return missing
