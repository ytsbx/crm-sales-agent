"""在产 SKU 的**权威字段、来源时间与差异确认**（第八批 §8.14）。

## 这一层为什么存在

`skus` 表上的列只告诉你"现在是什么值"，回答不了验收要问的三件事：

  · 这个值是**谁给的**？那条来源**核实过**没有？
  · 它**什么时候**变的？跟外部来源对得上吗？
  · 正式报价当时用的是**哪一版**经过人工确认的主数据？

所以这里的三张表（`sku_identity_sources` / `sku_field_authorities` /
`sku_master_versions`）把"值的来历"独立记下来。**它们与当前值并存，不互相覆盖。**

## 四条硬纪律

1. **外部来源不自动写回 `skus` 的列。**
   外部数据先落成"来源值 + 差异"，由带权限、带审计的确认动作决定谁生效。
   没有这一步，一次增量同步就能悄悄改掉正在报价的数据。

2. **不默认任一系统为主。**
   字段权威归属 `authority` 默认是空的（未拍板）。只有人显式登记才会填，
   并记下是谁、什么时候登记的。来源未核实时，接口显示的就是"待核实"。

3. **同步空值不抹人工销售资料。**
   增量里某个字段是空，而本地（或已确认版本）有值 → 生成 `null_overwrite` 差异，
   **本地一个字节都不动**。要采纳空值必须显式选"以外部为准"并写依据。

4. **同名不同码不自动合并；改码、停用不破坏历史。**
   名称命中但编码不同时只挂待确认；改码保留老身份行并指向新码（老编码仍能反查到
   同一个 SKU）；停用只改状态、不删行，历史确认版本快照与报价的行快照都不受影响。

## 真实对接在哪

一句话：**没有**。简道云/聚水潭的字段字典与授权都没有拿到（交接说明 §0.3 第 5 条），
所以 `ingest_external_sku` 是一个**契约明确但由外部调用方喂数据**的入口：
谁去取数（企业 erp-bridge / 官方 API）与取哪些字段，列为待外部验收。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.integration.model import IntegrationDiff
from app.modules.integration.vocab import (
    ALL_SHOPS,
    AUTH_CONFIRMED,
    AUTH_PENDING,
    AUTH_UNVERIFIED,
    DIFF_CODE_RENAME,
    DIFF_CONFIRMED_DIFFERS,
    DIFF_DOMAIN_SKU,
    DIFF_FIELD_CONFLICT,
    DIFF_IGNORED,
    DIFF_NULL_OVERWRITE,
    DIFF_OPEN,
    DIFF_PACKAGE_CONFLICT,
    DIFF_RESOLVED,
    DIFF_SAME_NAME_DIFF_CODE,
    DIFF_STOPPED_SOURCE,
    DIFF_UNIT_CONFLICT,
    DIFF_UNMATCHED_SKU_SOURCE,
    IDENTITY_CONFLICT,
    IDENTITY_MATCHED,
    IDENTITY_PENDING,
    IDENTITY_RENAMED,
    IDENTITY_STOPPED,
    RESOLUTION_DISABLE_LOCAL,
    RESOLUTION_IGNORE,
    RESOLUTION_KEEP_LOCAL,
    RESOLUTION_MANUAL,
    RESOLUTION_RENAME_LOCAL,
    RESOLUTION_TAKE_EXTERNAL,
)
from app.modules.product.model import Sku, SkuFieldAuthority, SkuIdentitySource, SkuMasterVersion

#: SKU 的**关键字段**白名单。只有这里的字段允许被确认动作写回 `skus`。
#: 为什么必须是白名单而不是"来什么写什么"：外部系统多给一个字段（比如 id、
#: 内部备注）就会被写进本地列，那是把外部数据结构当成本地数据模型。
MASTER_FIELDS: dict[str, str] = {
    "name": "名称",
    "specification": "规格",
    "color": "颜色",
    "material": "材质",
    "length": "长(mm)",
    "width": "宽(mm)",
    "height": "高(mm)",
    "weight": "单重(kg)",
    "carton_qty": "装箱数",
    "carton_volume": "箱体积(m³)",
    #: 用**中文**而不是 `MOQ`（2026-10-10，主人："很多人都不知道啥意思"）。
    #: 而且项目里本来就有中文说法：价格中心一直叫「起订量」——
    #: 同一个概念两套名字（价格中心「起订量」/ 产品「MOQ」）本身就该统一。
    "moq": "起订量",
    "package_type": "包装方式",
    "unit": "单位",
}

#: 字段类型：决定"以外部为准"时怎么写、以及比较时怎么比。
_TEXT_FIELDS = ("name", "specification", "color", "material", "package_type", "unit")
_INT_FIELDS = ("carton_qty", "moq")
_DECIMAL_FIELDS = ("length", "width", "height", "weight", "carton_volume")

#: 单位/包装相关的字段单独归类：它们的冲突比"名称不一致"严重得多
#: （会改变报价数量的含义），所以差异类型分开，不能和普通字段混在一个队列里。
UNIT_FIELDS = ("unit",)
PACKAGE_FIELDS = ("package_type", "carton_qty", "carton_volume")

#: 报价明细里**印给客户**的三个字段（§8.14 复审：只有这三个必须先确认）。
#:
#: 为什么是这三个：报价单上给客户看的"这条东西叫什么、什么规格、按什么单位算"。
#: 单位尤其关键 —— 它决定报价数量的含义（"10 件" vs "10 箱"）。
#: 其余字段（材质、重量、装箱数…）本地值照旧可用，未确认只如实提示、不拦发送；
#: 那个口径是 2026-10-07 定的"默认放行 + 如实提示"，本轮只把**这三个**收紧成硬闸门。
QUOTE_DISPLAY_FIELDS: tuple[str, ...] = ("name", "specification", "unit")

#: 每种差异允许的核定结论。
ALLOWED_RESOLUTIONS: dict[str, tuple[str, ...]] = {
    DIFF_UNIT_CONFLICT: (
        RESOLUTION_KEEP_LOCAL,
        RESOLUTION_TAKE_EXTERNAL,
        RESOLUTION_MANUAL,
    ),
    DIFF_PACKAGE_CONFLICT: (
        RESOLUTION_KEEP_LOCAL,
        RESOLUTION_TAKE_EXTERNAL,
        RESOLUTION_MANUAL,
    ),
    DIFF_FIELD_CONFLICT: (
        RESOLUTION_KEEP_LOCAL,
        RESOLUTION_TAKE_EXTERNAL,
        RESOLUTION_MANUAL,
    ),
    DIFF_CONFIRMED_DIFFERS: (
        RESOLUTION_KEEP_LOCAL,
        RESOLUTION_TAKE_EXTERNAL,
        RESOLUTION_MANUAL,
    ),
    DIFF_NULL_OVERWRITE: (RESOLUTION_KEEP_LOCAL, RESOLUTION_TAKE_EXTERNAL),
    DIFF_CODE_RENAME: (RESOLUTION_KEEP_LOCAL, RESOLUTION_RENAME_LOCAL),
    DIFF_STOPPED_SOURCE: (RESOLUTION_KEEP_LOCAL, RESOLUTION_DISABLE_LOCAL),
    # 同名不同码**只允许**"保留本地"或"人工裁定"：自动合并会把两家不同的货并成一条。
    DIFF_SAME_NAME_DIFF_CODE: (RESOLUTION_KEEP_LOCAL, RESOLUTION_MANUAL),
    DIFF_UNMATCHED_SKU_SOURCE: (RESOLUTION_MANUAL,),
}

#: 必须写依据才能核定的差异类型。
REQUIRE_NOTE = (
    DIFF_NULL_OVERWRITE,
    DIFF_CODE_RENAME,
    DIFF_STOPPED_SOURCE,
    DIFF_SAME_NAME_DIFF_CODE,
    DIFF_CONFIRMED_DIFFERS,
)

RESOLUTION_LABELS = {
    RESOLUTION_KEEP_LOCAL: "以本地为准（人工确认本地值）",
    RESOLUTION_TAKE_EXTERNAL: "以来源为准（写入本地并生成新确认版本）",
    RESOLUTION_MANUAL: "人工已处理（不动主数据）",
    RESOLUTION_IGNORE: "不是差异",
    RESOLUTION_RENAME_LOCAL: "按来源新编码改本地 SKU 编码（老编码保留可反查）",
    RESOLUTION_DISABLE_LOCAL: "按来源停用本地 SKU（不删除、历史快照不动）",
}

#: 会写回 `skus` 列的结论。其余结论只改来源/差异记录。
WRITING_RESOLUTIONS = (
    RESOLUTION_TAKE_EXTERNAL,
    RESOLUTION_RENAME_LOCAL,
    RESOLUTION_DISABLE_LOCAL,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_value(value: Any) -> Any:
    """把字段值转成能进 JSON 列、又能与本地值直接比较的形状。"""
    if isinstance(value, Decimal):
        return float(value)
    return value


def normalize_field_value(field: str, value: Any) -> Any:
    """按字段类型归一；类型不对**当场报错**，不静默转成 None。

    为什么较真：把 `"abc"` 静默当成 None 写进名称，看起来"同步成功"，
    实际是把对方的脏数据洗成了"本地没有这个值"。
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        # 空串与 None 是同一件事：**增量里的空值**，不能当成"明确要清空"。
        return None
    if field in _TEXT_FIELDS:
        return str(value).strip() or None
    if field in _INT_FIELDS:
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"字段 {field}（{MASTER_FIELDS[field]}）需要一个整数，收到 {value!r}",
                422,
            ) from error
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"字段 {field}（{MASTER_FIELDS[field]}）需要一个数字，收到 {value!r}",
                422,
            ) from error
    raise AppError(
        ErrorCode.PARAM_ERROR,
        f"字段 {field} 不在 SKU 关键字段白名单里（{'、'.join(MASTER_FIELDS)}）："
        "不允许把外部给的任意字段写进本地主数据",
        422,
    )


def local_value(sku: Sku, field: str) -> Any:
    return _json_value(getattr(sku, field, None))


def _diff_conflict_type(field: str) -> str:
    if field in UNIT_FIELDS:
        return DIFF_UNIT_CONFLICT
    if field in PACKAGE_FIELDS:
        return DIFF_PACKAGE_CONFLICT
    return DIFF_FIELD_CONFLICT


def build_sku_diff_key(
    *,
    system_type: str,
    shop_id: str,
    sku_id: int | None,
    external_code: str,
    diff_type: str,
    field_name: str | None = None,
    values: Any = None,
) -> str:
    """SKU 主数据差异的稳定指纹（同 §8.13：值变了才出新差异）。"""
    payload = {
        "domain": DIFF_DOMAIN_SKU,
        "system_type": system_type,
        "shop_id": shop_id,
        "sku_id": "" if sku_id is None else str(sku_id),
        "external_code": external_code,
        "diff_type": diff_type,
        "field_name": field_name or "",
    }
    if values is not None:
        raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
        payload["values"] = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "skudiff-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:48]


async def _upsert_diff(session: AsyncSession, fields: dict[str, Any]) -> tuple[IntegrationDiff, str]:
    row = (
        await session.execute(
            select(IntegrationDiff).where(IntegrationDiff.diff_key == fields["diff_key"])
        )
    ).scalars().first()
    if row is None:
        row = IntegrationDiff(**fields, status=DIFF_OPEN)
        session.add(row)
        await session.flush()
        return row, "created"
    changed = False
    for key in ("current_value", "incoming_value", "evidence", "internal_id", "field_name"):
        if key in fields and getattr(row, key) != fields[key]:
            setattr(row, key, fields[key])
            changed = True
    await session.flush()
    return row, "updated" if changed else "unchanged"


def serialize_identity(row: SkuIdentitySource) -> dict:
    return {
        "id": row.id,
        "sku_id": row.sku_id,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "external_code": row.external_code,
        "external_name": row.external_name,
        "match_status": row.match_status,
        "match_basis": row.match_basis,
        "superseded_by_code": row.superseded_by_code,
        "source_updated_at": row.source_updated_at,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "note": row.note,
        "has_pending_payload": bool(row.source_payload),
    }


def serialize_version(row: SkuMasterVersion) -> dict:
    return {
        "id": row.id,
        "sku_id": row.sku_id,
        "version_no": row.version_no,
        "values": row.values,
        "source_summary": row.source_summary,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at,
        "note": row.note,
        "diff_key": row.diff_key,
    }


async def get_sku_or_404(session: AsyncSession, sku_id: int) -> Sku:
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU #{sku_id} 不存在", 404)
    return sku


async def _find_identity(
    session: AsyncSession, *, system_type: str, shop_id: str, external_code: str
) -> SkuIdentitySource | None:
    return (
        await session.execute(
            select(SkuIdentitySource).where(
                SkuIdentitySource.system_type == system_type,
                SkuIdentitySource.shop_id == shop_id,
                SkuIdentitySource.external_code == external_code,
            )
        )
    ).scalars().first()


async def _match_identity(
    session: AsyncSession, source: SkuIdentitySource
) -> tuple[int | None, str | None, str]:
    """按**明规则**给外部身份找本地 SKU。

    规则 1：本地 SKU 编码与外部编码完全一致 → 匹配。
    规则 2（**只提示、不合并**）：名称在本地唯一命中，但编码不同 → 返回 conflict，
    由 `same_name_diff_code` 差异交人工裁定。"三系统同名不同码不自动合并"就是这条。
    """
    exact = (
        await session.execute(
            select(Sku).where(
                Sku.sku_code == source.external_code, Sku.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    if len(exact) == 1:
        return exact[0].id, "external_code", IDENTITY_MATCHED
    if len(exact) > 1:
        return None, None, IDENTITY_CONFLICT
    if source.external_name:
        by_name = (
            await session.execute(
                select(Sku).where(
                    Sku.name == source.external_name, Sku.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        if len(by_name) == 1:
            return by_name[0].id, "name_unique", IDENTITY_CONFLICT
        if len(by_name) > 1:
            return None, None, IDENTITY_CONFLICT
    return None, None, IDENTITY_PENDING


async def _source_verified(
    session: AsyncSession, *, system_type: str, shop_id: str
) -> bool:
    """该来源是不是已核实过的（读 `external_source_registry` 的 sku_master 行）。"""
    from app.modules.erp.collection import get_source_state

    state = await get_source_state(
        session, system_type=system_type, shop_id=shop_id, source_kind="sku_master"
    )
    return bool(state["verified"])


async def _authority_for(
    session: AsyncSession, *, sku_id: int, field_name: str
) -> SkuFieldAuthority | None:
    return (
        await session.execute(
            select(SkuFieldAuthority).where(
                SkuFieldAuthority.sku_id == sku_id,
                SkuFieldAuthority.field_name == field_name,
            )
        )
    ).scalars().first()


async def _raise_field_diffs(
    session: AsyncSession,
    *,
    source: SkuIdentitySource,
    sku: Sku,
    field: str,
    incoming: Any,
    now: datetime,
) -> tuple[int, str]:
    """为单个字段生成/更新差异。返回 `(差异条数, 判决)`。

    判决只有三种：
      · `"same"`     两边一致，无事发生；
      · `"empty"`    增量给了空值 → `null_overwrite`（**不覆盖**）；
      · `"differs"`  值不同 → 按字段归类成单位/包装/普通冲突，或"与已确认版本不同"。
    """
    authority = await _authority_for(session, sku_id=sku.id, field_name=field)
    current = local_value(sku, field)
    confirmed = authority.confirmed_value if authority is not None else None
    confirmed_version = authority.confirmed_version if authority is not None else 0

    created = 0
    if incoming is None:
        if current is None and confirmed is None:
            return 0, "same"
        # **增量空值不抹人工销售资料**：本地有值（或已确认过值）时只挂差异。
        values = {"current": current, "confirmed": confirmed}
        await _upsert_diff(
            session,
            {
                "diff_key": build_sku_diff_key(
                    system_type=source.system_type,
                    shop_id=source.shop_id,
                    sku_id=sku.id,
                    external_code=source.external_code,
                    diff_type=DIFF_NULL_OVERWRITE,
                    field_name=field,
                    values=values,
                ),
                "domain": DIFF_DOMAIN_SKU,
                "system_type": source.system_type,
                "shop_id": source.shop_id,
                "object_type": "sku",
                "internal_id": sku.id,
                "external_id": source.external_code,
                "field_name": field,
                "diff_type": DIFF_NULL_OVERWRITE,
                "current_value": current,
                "incoming_value": None,
                "evidence": {
                    "identity_source_id": source.id,
                    "external_code": source.external_code,
                    "external_name": source.external_name,
                    "source_updated_at": (
                        source.source_updated_at.isoformat()
                        if source.source_updated_at
                        else None
                    ),
                    "confirmed_value": confirmed,
                    "note": "来源上报了空值；本地/已确认版本有值，按不覆盖处理，等人工确认",
                },
            },
        )
        created += 1
        return created, "empty"

    if confirmed_version and confirmed is not None and _json_value(incoming) != confirmed:
        values = {"confirmed": confirmed, "incoming": _json_value(incoming)}
        await _upsert_diff(
            session,
            {
                "diff_key": build_sku_diff_key(
                    system_type=source.system_type,
                    shop_id=source.shop_id,
                    sku_id=sku.id,
                    external_code=source.external_code,
                    diff_type=DIFF_CONFIRMED_DIFFERS,
                    field_name=field,
                    values=values,
                ),
                "domain": DIFF_DOMAIN_SKU,
                "system_type": source.system_type,
                "shop_id": source.shop_id,
                "object_type": "sku",
                "internal_id": sku.id,
                "external_id": source.external_code,
                "field_name": field,
                "diff_type": DIFF_CONFIRMED_DIFFERS,
                "current_value": current,
                "incoming_value": _json_value(incoming),
                "evidence": {
                    "identity_source_id": source.id,
                    "external_code": source.external_code,
                    "confirmed_version": confirmed_version,
                    "confirmed_value": confirmed,
                    "local_value": current,
                    "note": "来源值与已人工确认的版本不一致：改它等于改一个已经用过的口径",
                },
            },
        )
        created += 1
        return created, "differs"

    if _json_value(incoming) == current:
        return 0, "same"

    diff_type = _diff_conflict_type(field)
    values = {"current": current, "incoming": _json_value(incoming)}
    await _upsert_diff(
        session,
        {
            "diff_key": build_sku_diff_key(
                system_type=source.system_type,
                shop_id=source.shop_id,
                sku_id=sku.id,
                external_code=source.external_code,
                diff_type=diff_type,
                field_name=field,
                values=values,
            ),
            "domain": DIFF_DOMAIN_SKU,
            "system_type": source.system_type,
            "shop_id": source.shop_id,
            "object_type": "sku",
            "internal_id": sku.id,
            "external_id": source.external_code,
            "field_name": field,
            "diff_type": diff_type,
            "current_value": current,
            "incoming_value": _json_value(incoming),
            "evidence": {
                "identity_source_id": source.id,
                "external_code": source.external_code,
                "external_name": source.external_name,
                "source_updated_at": (
                    source.source_updated_at.isoformat()
                    if source.source_updated_at
                    else None
                ),
                "note": "来源值与本地当前值不一致，未自动覆盖",
            },
        },
    )
    created += 1
    return created, "differs"


async def _materialize_fields(
    session: AsyncSession,
    *,
    source: SkuIdentitySource,
    sku: Sku,
    fields: dict[str, Any],
    source_verified: bool,
    now: datetime,
) -> dict:
    """把来源给的字段落成"来源值 + 差异"，**一个字段都不写回 `skus`**。"""
    conflicts = 0
    empties = 0
    same = 0
    for field, raw in fields.items():
        if field not in MASTER_FIELDS:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"字段 {field} 不是 SKU 关键字段（可同步：{'、'.join(MASTER_FIELDS)}）",
                422,
            )
        incoming = normalize_field_value(field, raw)
        authority = await _authority_for(session, sku_id=sku.id, field_name=field)
        if authority is None:
            authority = SkuFieldAuthority(sku_id=sku.id, field_name=field)
            session.add(authority)
            await session.flush()
        authority.source_system = source.system_type
        authority.source_verified = source_verified
        authority.external_identity = source.external_code
        authority.source_updated_at = source.source_updated_at
        authority.source_value = _json_value(incoming)
        await session.flush()

        _, verdict = await _raise_field_diffs(
            session, source=source, sku=sku, field=field, incoming=incoming, now=now
        )
        if verdict == "differs":
            conflicts += 1
            authority.status = AUTH_PENDING
        elif verdict == "empty":
            empties += 1
            authority.status = AUTH_PENDING
        else:
            same += 1
            if authority.confirmed_version:
                authority.status = AUTH_CONFIRMED
            else:
                authority.status = AUTH_UNVERIFIED
    return {"conflicts": conflicts, "empty_increments": empties, "unchanged": same}


async def ingest_external_sku(
    session: AsyncSession,
    *,
    system_type: str,
    external_code: str,
    external_name: str | None = None,
    fields: dict[str, Any] | None = None,
    shop_id: str | None = None,
    source_updated_at: Any = None,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """接收一条外部 SKU 来源数据（真实来源接入前提是拿到字段字典，列为待外部验收）。

    流程：登记身份 → 尝试匹配本地 SKU →（匹配上）落来源值与差异 /（没匹配上）
    存原始值待匹配。**任何情况下都不直接改 `skus`。**

    **由本函数自己提交**：它是集成入口（桥接脚本可能直接调用，不经路由），
    "收到一条来源数据"就是一次完整的写入单元；只 flush 不提交的话，
    调用方一旦结束会话就会把差异一起回滚掉，而队列里看起来像"什么都没发生"。
    用户手动动作（核定、改码、停用、定归属）则相反：由路由统一提交，
    好让审计记录与业务改动落在同一个事务里。
    """
    moment = now or _now()
    code = _text(external_code)
    if not code:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "external_code 必填", 422)
    # 字段名先全部校验一遍：不认识的字段要**在写任何东西之前**报错，
    # 否则会出现"前三个字段落了库、第四个字段报错"的半截状态，
    # 而且身份行已经建出来了，重试还会撞唯一键。
    unknown = [field for field in fields if field not in MASTER_FIELDS]
    if unknown:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不是 SKU 关键字段：{'、'.join(unknown)}（可同步：{'、'.join(MASTER_FIELDS)}）",
            422,
        )
    shop = (shop_id or "").strip() or ALL_SHOPS
    fields = dict(fields or {})
    updated_at = source_updated_at
    if isinstance(updated_at, str):
        try:
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            updated_at = None
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)

    source = await _find_identity(
        session, system_type=system_type, shop_id=shop, external_code=code
    )
    if source is None:
        source = SkuIdentitySource(
            system_type=system_type,
            shop_id=shop,
            external_code=code,
            first_seen_at=moment,
            last_seen_at=moment,
        )
        session.add(source)
        await session.flush()
    source.external_name = _text(external_name) or source.external_name
    source.source_updated_at = updated_at or source.source_updated_at
    source.last_seen_at = moment

    # 字段名已在开头校验过，这里只做类型归一。
    normalized = {
        field: _json_value(normalize_field_value(field, value))
        for field, value in fields.items()
    }

    if source.sku_id is None:
        sku_id, basis, status = await _match_identity(session, source)
        source.match_status = status
        source.match_basis = basis
        if sku_id is not None and status == IDENTITY_MATCHED:
            source.sku_id = sku_id
        elif status == IDENTITY_CONFLICT:
            await _raise_same_name_diff(session, source=source)

    verified = await _source_verified(session, system_type=system_type, shop_id=shop)
    result: dict[str, Any] = {
        "identity": serialize_identity(source),
        "source_verified": verified,
        "matched_sku_id": source.sku_id,
        "applied_to_local": False,
    }

    if source.sku_id is None:
        # 还没匹配上：原始字段值存在身份行里，等本地补建 SKU 后重放（replay）。
        source.source_payload = normalized
        if source.match_status != IDENTITY_CONFLICT:
            # 同名不同码已经有一条更具体的差异了，不再叠加一条泛化的"待匹配"——
            # 同一个问题在队列里出两条，人就会开始忽略这个队列。
            await _raise_unmatched_diff(session, source=source)
        result["message"] = (
            f"{system_type}/{shop} 的 SKU {code} 还没匹配到本地 SKU："
            "来源值已留存，本地补建后跑一次重放即可落成字段权威（不必重新取数）"
        )
        await session.commit()
        return result

    sku = await get_sku_or_404(session, source.sku_id)
    if not normalized:
        source.source_payload = None
    else:
        counts = await _materialize_fields(
            session, source=source, sku=sku, fields=normalized,
            source_verified=verified, now=moment,
        )
        source.source_payload = None
        result.update(counts)
        result["message"] = (
            f"已登记来源值：字段冲突 {counts['conflicts']} 个、"
            f"增量空值 {counts['empty_increments']} 个、无变化 {counts['unchanged']} 个；"
            "本地 SKU 没有被自动改写，差异请在队列里核定"
        )
    # 提交：见 docstring —— 集成入口的"收到即落地"。
    await session.commit()
    return result


async def _raise_same_name_diff(
    session: AsyncSession, *, source: SkuIdentitySource
) -> None:
    """同名不同码（或同名多家）：挂差异，**绝不自动合并**。"""
    candidates = (
        await session.execute(
            select(Sku).where(Sku.name == source.external_name, Sku.deleted_at.is_(None))
        )
    ).scalars().all()
    values = {
        "external_code": source.external_code,
        "external_name": source.external_name,
        "local_sku_codes": sorted(row.sku_code for row in candidates),
    }
    await _upsert_diff(
        session,
        {
            "diff_key": build_sku_diff_key(
                system_type=source.system_type,
                shop_id=source.shop_id,
                sku_id=candidates[0].id if len(candidates) == 1 else None,
                external_code=source.external_code,
                diff_type=DIFF_SAME_NAME_DIFF_CODE,
                values=values,
            ),
            "domain": DIFF_DOMAIN_SKU,
            "system_type": source.system_type,
            "shop_id": source.shop_id,
            "object_type": "sku",
            "internal_id": candidates[0].id if len(candidates) == 1 else None,
            "external_id": source.external_code,
            "field_name": None,
            "diff_type": DIFF_SAME_NAME_DIFF_CODE,
            "current_value": values["local_sku_codes"],
            "incoming_value": source.external_code,
            "evidence": {
                "identity_source_id": source.id,
                "external_name": source.external_name,
                "local_candidates": [
                    {"id": row.id, "sku_code": row.sku_code, "name": row.name}
                    for row in candidates
                ],
                "note": "名称一致但编码不同：三系统同名不同码不自动合并，必须人工裁定",
            },
        },
    )


async def _raise_unmatched_diff(
    session: AsyncSession, *, source: SkuIdentitySource
) -> None:
    values = {"external_code": source.external_code, "external_name": source.external_name}
    await _upsert_diff(
        session,
        {
            "diff_key": build_sku_diff_key(
                system_type=source.system_type,
                shop_id=source.shop_id,
                sku_id=None,
                external_code=source.external_code,
                diff_type=DIFF_UNMATCHED_SKU_SOURCE,
                values=values,
            ),
            "domain": DIFF_DOMAIN_SKU,
            "system_type": source.system_type,
            "shop_id": source.shop_id,
            "object_type": "sku",
            "internal_id": None,
            "external_id": source.external_code,
            "field_name": None,
            "diff_type": DIFF_UNMATCHED_SKU_SOURCE,
            "current_value": None,
            "incoming_value": values,
            "evidence": {
                "identity_source_id": source.id,
                "source_payload": source.source_payload,
                "note": "本地还没有这条 SKU：待匹配，补建后重放即可（不必重新取数）",
            },
        },
    )


async def replay_identity_source(
    session: AsyncSession,
    source: SkuIdentitySource,
    *,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """待匹配的外部身份重放一次：本地补建 SKU 之后把留存的来源值落成字段权威。"""
    moment = now or _now()
    if source.sku_id is not None:
        return {
            "identity": serialize_identity(source),
            "replayed": False,
            "message": "这条外部身份已经匹配过了，未重复处理",
        }
    sku_id, basis, status = await _match_identity(session, source)
    source.match_status = status
    source.match_basis = basis
    if sku_id is None or status != IDENTITY_MATCHED:
        if status == IDENTITY_CONFLICT:
            await _raise_same_name_diff(session, source=source)
        await session.flush()
        return {
            "identity": serialize_identity(source),
            "replayed": False,
            "message": (
                "仍然匹配不上："
                + ("名称命中但编码不同/命中多条，需人工裁定" if status == IDENTITY_CONFLICT
                   else "本地没有编码或名称对应的 SKU")
            ),
        }
    source.sku_id = sku_id
    sku = await get_sku_or_404(session, sku_id)
    verified = await _source_verified(
        session, system_type=source.system_type, shop_id=source.shop_id
    )
    counts = await _materialize_fields(
        session,
        source=source,
        sku=sku,
        fields=dict(source.source_payload or {}),
        source_verified=verified,
        now=moment,
    )
    source.source_payload = None
    await session.flush()
    return {
        "identity": serialize_identity(source),
        "replayed": True,
        "counts": counts,
        "message": "重放完成：来源值已落成字段权威与差异（本地 SKU 仍未自动改写）",
    }


async def rename_identity_source(
    session: AsyncSession,
    source: SkuIdentitySource,
    *,
    new_external_code: str,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """来源改码：**老身份行保留**（标 renamed 并指向新码），新码另起一行。

    为什么不能直接改 `external_code`：改掉以后"用老编码来查"就查不到了，
    而历史报价/单据上印着老编码。保留历史行，老编码依然能反查到同一个 SKU。
    """
    moment = now or _now()
    new_code = _text(new_external_code)
    if not new_code:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "new_external_code 必填", 422)
    if new_code == source.external_code:
        raise AppError(ErrorCode.PARAM_ERROR, "新编码与当前编码相同，无需改码", 422)
    existing = await _find_identity(
        session,
        system_type=source.system_type,
        shop_id=source.shop_id,
        external_code=new_code,
    )
    if existing is not None and existing.id != source.id:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"{source.system_type} 里已经存在编码 {new_code} 的身份记录"
            f"（sku_id={existing.sku_id}）：先确认是不是同一个 SKU，不要盲目合并",
            409,
        )

    clone = SkuIdentitySource(
        sku_id=source.sku_id,
        system_type=source.system_type,
        shop_id=source.shop_id,
        external_code=new_code,
        external_name=source.external_name,
        match_status=source.match_status if source.sku_id is None else IDENTITY_MATCHED,
        match_basis=source.match_basis,
        source_payload=source.source_payload,
        source_updated_at=moment,
        first_seen_at=moment,
        last_seen_at=moment,
        note=f"由 {source.external_code} 改码而来",
    )
    session.add(clone)
    await session.flush()
    source.match_status = IDENTITY_RENAMED
    source.superseded_by_code = new_code
    source.note = f"已改码为 {new_code}（老编码保留可反查）"
    await session.flush()

    values = {"old_code": source.external_code, "new_code": new_code, "sku_id": source.sku_id}
    await _upsert_diff(
        session,
        {
            "diff_key": build_sku_diff_key(
                system_type=source.system_type,
                shop_id=source.shop_id,
                sku_id=source.sku_id,
                external_code=new_code,
                diff_type=DIFF_CODE_RENAME,
                values=values,
            ),
            "domain": DIFF_DOMAIN_SKU,
            "system_type": source.system_type,
            "shop_id": source.shop_id,
            "object_type": "sku",
            "internal_id": source.sku_id,
            "external_id": new_code,
            "field_name": None,
            "diff_type": DIFF_CODE_RENAME,
            "current_value": source.external_code,
            "incoming_value": new_code,
            "evidence": {
                "old_identity_source_id": source.id,
                "new_identity_source_id": clone.id,
                "note": (
                    "来源改码：本地 SKU 编码**没有**被自动改；要改请选"
                    "「按来源新编码改本地 SKU 编码」，老编码会留成可反查的历史"
                ),
            },
        },
    )
    return {
        "old_identity": serialize_identity(source),
        "new_identity": serialize_identity(clone),
        "applied_to_local": False,
        "message": f"已登记改码 {source.external_code} → {new_code}；本地 SKU 编码未自动修改",
    }


async def mark_identity_stopped(
    session: AsyncSession,
    source: SkuIdentitySource,
    *,
    note: str | None = None,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """来源标记停用：挂差异，**本地 SKU 状态不动**（停用要人工确认）。"""
    source.match_status = IDENTITY_STOPPED
    source.note = note or "来源标记停用（本地未自动停用）"
    await session.flush()
    values = {"external_code": source.external_code, "sku_id": source.sku_id}
    await _upsert_diff(
        session,
        {
            "diff_key": build_sku_diff_key(
                system_type=source.system_type,
                shop_id=source.shop_id,
                sku_id=source.sku_id,
                external_code=source.external_code,
                diff_type=DIFF_STOPPED_SOURCE,
                values=values,
            ),
            "domain": DIFF_DOMAIN_SKU,
            "system_type": source.system_type,
            "shop_id": source.shop_id,
            "object_type": "sku",
            "internal_id": source.sku_id,
            "external_id": source.external_code,
            "field_name": None,
            "diff_type": DIFF_STOPPED_SOURCE,
            "current_value": None,
            "incoming_value": "stopped",
            "evidence": {
                "identity_source_id": source.id,
                "note": note or "来源停用",
                "reason": "停用本地 SKU 会影响在售判断；历史报价的行快照与确认版本不会被改",
            },
        },
    )
    return {
        "identity": serialize_identity(source),
        "applied_to_local": False,
        "message": "已登记来源停用；本地 SKU 状态未自动改变，请在差异队列里确认",
    }


async def set_field_authority(
    session: AsyncSession,
    *,
    sku_id: int,
    field_name: str,
    authority: str | None,
    operator_id: int | None,
    now: datetime | None = None,
) -> dict:
    """显式登记**字段权威归属**。

    默认是空的（未拍板）。这个入口存在的意义正是"不默认任一系统为主"：
    要归属谁，得有人说清楚并留痕（谁、什么时候）。
    """
    if field_name not in MASTER_FIELDS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"字段 {field_name} 不是 SKU 关键字段（{'、'.join(MASTER_FIELDS)}）",
            422,
        )
    sku = await get_sku_or_404(session, sku_id)
    moment = now or _now()
    row = await _authority_for(session, sku_id=sku.id, field_name=field_name)
    if row is None:
        row = SkuFieldAuthority(sku_id=sku.id, field_name=field_name)
        session.add(row)
        await session.flush()
    row.authority = _text(authority)
    row.authority_set_by = operator_id if row.authority else None
    row.authority_set_at = moment if row.authority else None
    await session.flush()
    return {
        "sku_id": sku.id,
        "field_name": field_name,
        "authority": row.authority,
        "authority_label": row.authority or "未拍板",
        "message": (
            f"字段 {MASTER_FIELDS[field_name]} 的权威归属已登记为 {row.authority}"
            if row.authority
            else f"字段 {MASTER_FIELDS[field_name]} 的权威归属已清空（未拍板）"
        ),
    }


# ---------------------------------------------------------------- 确认与版本


async def _next_version_no(session: AsyncSession, *, sku_id: int) -> int:
    current = (
        await session.execute(
            select(func.max(SkuMasterVersion.version_no)).where(
                SkuMasterVersion.sku_id == sku_id
            )
        )
    ).scalar_one()
    return int(current or 0) + 1


async def _snapshot_version(
    session: AsyncSession,
    *,
    sku: Sku,
    operator_id: int | None,
    note: str | None,
    diff_key: str | None,
    now: datetime,
) -> SkuMasterVersion:
    """把"当前已确认的字段值"冻结成一版快照（正式报价回溯用）。"""
    rows = (
        await session.execute(
            select(SkuFieldAuthority).where(
                SkuFieldAuthority.sku_id == sku.id,
                SkuFieldAuthority.confirmed_version > 0,
            )
        )
    ).scalars().all()
    values = {row.field_name: row.confirmed_value for row in rows}
    source_summary = {
        row.field_name: {
            "source_system": row.source_system,
            "source_verified": bool(row.source_verified),
            "external_identity": row.external_identity,
            "confirmed_version": row.confirmed_version,
        }
        for row in rows
    }
    version = SkuMasterVersion(
        sku_id=sku.id,
        version_no=await _next_version_no(session, sku_id=sku.id),
        values=values,
        source_summary=source_summary,
        confirmed_by=operator_id,
        confirmed_at=now,
        note=note,
        diff_key=diff_key,
    )
    session.add(version)
    await session.flush()
    return version


async def _maybe_snapshot(
    session: AsyncSession,
    *,
    sku: Sku,
    operator_id: int | None,
    note: str | None,
    diff_key: str | None,
    now: datetime,
) -> SkuMasterVersion | None:
    """有已确认字段时才冻结版本。

    为什么要有这个判断：像"同名不同码"这种没有字段的差异，选"保留本地"并不
    确认任何字段值；那时生成一个 `values={}` 的空版本只会污染版本列表，
    让"正式报价用的哪一版"更难看懂。
    """
    confirmed = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SkuFieldAuthority)
                .where(
                    SkuFieldAuthority.sku_id == sku.id,
                    SkuFieldAuthority.confirmed_version > 0,
                )
            )
        ).scalar_one()
    )
    if not confirmed:
        return None
    return await _snapshot_version(
        session,
        sku=sku,
        operator_id=operator_id,
        note=note,
        diff_key=diff_key,
        now=now,
    )


async def _confirm_field(
    session: AsyncSession,
    *,
    sku: Sku,
    field_name: str,
    value: Any,
    operator_id: int | None,
    now: datetime,
    dedupe: bool = False,
) -> tuple[SkuFieldAuthority, bool]:
    """确认一个字段的值为**当前本地值**。返回 `(行, 这次是否真的变了)`。

    `dedupe=True` 时，值没变就**不递增 `confirmed_version`、不改 `confirmed_at`、
    不改 `confirmed_by`** —— "确认次数"与"确认时间"表达的是**确认值发生过变化**，
    反复点同一件事不该把它们推着走（issue 建议第 3 条）。
    逐字段独立确认也靠它：只把这次真的改了的字段算一次确认。

    调用方（`confirm_sku_diff`）不传 `dedupe`，保持原行为：差异核定本身是
    一次有依据的人工裁定，即使值恰好相同也是一次确认动作。
    """
    row = await _authority_for(session, sku_id=sku.id, field_name=field_name)
    if row is None:
        row = SkuFieldAuthority(sku_id=sku.id, field_name=field_name)
        session.add(row)
        await session.flush()
    incoming = _json_value(value)
    unchanged = (
        dedupe
        and (row.confirmed_version or 0) > 0
        and _normalize_for_compare(row.confirmed_value) == _normalize_for_compare(incoming)
    )
    if unchanged:
        return row, False
    row.confirmed_value = incoming
    row.confirmed_version = (row.confirmed_version or 0) + 1
    row.confirmed_by = operator_id
    row.confirmed_at = now
    row.status = AUTH_CONFIRMED
    await session.flush()
    return row, True


def _normalize_for_compare(value: Any) -> str:
    """把确认值归一成可比较的字符串。

    为什么不能直接比：`confirmed_value` 是 JSONB，`None` 与 `""` 都要能与
    本地值如实对照；数字 `30` 与 `"30"` 在不同来源下含义相同，不该因为类型
    差异被当成"变了"而多记一次确认。
    """
    if value is None:
        return ""
    return str(value)


def _validate_resolution(diff: IntegrationDiff, resolution: str, note: str | None) -> None:
    allowed = ALLOWED_RESOLUTIONS.get(diff.diff_type)
    if allowed is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"主数据差异类型 {diff.diff_type} 没有定义允许的核定结论，先明确口径再核定",
            422,
        )
    if resolution not in allowed:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{diff.diff_type} 只允许这些结论：{'、'.join(allowed)}；收到 {resolution}",
            422,
        )
    if diff.diff_type in REQUIRE_NOTE and not (note or "").strip():
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            f"{diff.diff_type} 必须写清依据（谁给的来源、为什么改本地口径）",
            422,
        )


async def confirm_local_master(
    session: AsyncSession,
    *,
    sku_id: int,
    fields: tuple[str, ...] = QUOTE_DISPLAY_FIELDS,
    note: str | None = None,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """**本地直接确认**：有权限的人核对本地值后，一次确认这些字段并冻结一版快照。

    为什么必须有这个入口（审查 2026-10-10 实测的 P1 流程阻断）：

        - 本地新建 SKU **不会**产生确认记录、也不产生快照（实测 `SkuFieldAuthority`
          0 条、`sku_master_versions` 0 版）；
        - 唯一的确认动作挂在**差异记录**上（`confirm_sku_diff`），
          而"登记权威归属"（`set_field_authority`）只写 `authority`、
          **不碰 `confirmed_version`**；
        - 于是本地自建的 SKU 没有可引用的已确认版本 →
          正式发送时 `quoted_snapshot_problems` 返回
          「（没有可引用的已确认主数据版本）」→ **正式报价被硬拦，且无路可走**。

    ⚠️ "从外部导入一次"**不是**可靠出口：来源值与本地完全一致时不产生差异，
    也就仍然没有可点的核定入口（实测 zero-diff）。

    与 `confirm_sku_diff` 的 `RESOLUTION_KEEP_LOCAL` **同源同口径**：
    确认的都是**当前本地值**（`local_value`），都锁 SKU 行、都冻结一版快照。

    三个字段**一起确认**（主人 2026-10-10 拍板）：不允许出现"确认了名称、
    规格还没确认"的半截状态 —— 那正是正式发送被拦时最难解释的情况。
    但**值允许为空**：`""` 是合法的已确认值（例如没有规格的产品），
    判据是"这个 key 有没有进快照"，不是"值非不非空" —— 与
    `quoted_snapshot_problems` 的判据保持一致。
    """
    wanted = list(fields)
    unknown = [f for f in wanted if f not in MASTER_FIELDS]
    if unknown:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "不是 SKU 关键字段：" + "、".join(unknown),
            422,
        )
    moment = now or _now()
    # 锁 SKU 行：两个并发确认会同时算 version_no，靠行锁 + 唯一约束才能只出一版
    #（与 `confirm_sku_diff` 同一手法）
    sku = (
        await session.execute(select(Sku).where(Sku.id == sku_id).with_for_update())
    ).scalars().first()
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU #{sku_id} 不存在", 404)

    # 逐个确认**当前本地值**（与 RESOLUTION_KEEP_LOCAL 同一件事）。
    # `dedupe=True`：值没变的字段不算一次新确认（逐字段独立确认，第 2/3 条）。
    changed: list[str] = []
    for field in wanted:
        _row, did_change = await _confirm_field(
            session,
            sku=sku,
            field_name=field,
            value=local_value(sku, field),
            operator_id=operator_id,
            now=moment,
            dedupe=True,
        )
        if did_change:
            changed.append(field)
    after = await _confirmed_values(session, sku_id=sku.id, fields=wanted)
    # ⚠️ **值没变就不再冻结一版**：重复点"确认"会一路垒出 V2/V3/V4 而内容完全一样，
    # 版本列表越来越难看懂，而"正式报价用的哪一版"正是靠这个列表回答的。
    # 与 `_maybe_snapshot` 不为空差异造版本的道理相同。
    #
    # 但"有人又核对过一次"这件事**要留痕**（主人 2026-10-10 拍板）：
    # 不改 `confirmed_at`/`confirmed_version`（那是"确认值变过"的记录），
    # 而是在审计里写一条 `sku_master_recheck` —— 与 `sku_master_confirm_local`
    # 分开，这样"确认过几次"与"核对过几次"两个问题都答得出来。
    if not changed:
        existing = await confirmed_master_version(session, sku.id)
        return {
            "sku_id": sku.id,
            "sku_code": sku.sku_code,
            "confirmed_fields": wanted,
            "confirmed_labels": [MASTER_FIELDS[f] for f in wanted],
            "values": after,
            "changed_fields": [],
            "changed_labels": [],
            "version_no": (existing or {}).get("version_no"),
            "version_id": (existing or {}).get("id"),
            "confirmed_by": operator_id,
            "confirmed_at": moment,
            "note": note,
            "snapshot_created": False,
            #: True = 这次只是"又核对了一遍"，没有改变任何确认值。
            #: 调用方据此写 `sku_master_recheck` 审计（而不是 confirm 审计）。
            "recheck": True,
            "message": (
                "主数据与已确认版本一致，无需重新确认"
                + (
                    f"（当前已确认版本：第 {(existing or {}).get('version_no')} 版）"
                    if existing
                    else ""
                )
            ),
        }
    version = await _snapshot_version(
        session,
        sku=sku,
        operator_id=operator_id,
        note=note or "本地直接确认",
        diff_key=None,
        now=moment,
    )
    return {
        "sku_id": sku.id,
        "sku_code": sku.sku_code,
        "confirmed_fields": wanted,
        "confirmed_labels": [MASTER_FIELDS[f] for f in wanted],
        "values": after,
        "changed_fields": changed,
        "changed_labels": [MASTER_FIELDS[f] for f in changed],
        "version_no": version.version_no,
        "version_id": version.id,
        "confirmed_by": operator_id,
        "confirmed_at": moment,
        "note": version.note,
        "snapshot_created": True,
        "recheck": False,
        "message": (
            "已确认主数据（"
            + "、".join(MASTER_FIELDS[f] for f in wanted)
            + f"）并冻结为第 {version.version_no} 版快照"
        ),
    }


async def _confirmed_values(
    session: AsyncSession, *, sku_id: int, fields: list[str]
) -> dict[str, Any]:
    """取这些字段**当前已确认**的值（只为对比"这次确认改了什么"）。"""
    rows = (
        await session.execute(
            select(SkuFieldAuthority).where(
                SkuFieldAuthority.sku_id == sku_id,
                SkuFieldAuthority.field_name.in_(fields),
                SkuFieldAuthority.confirmed_version > 0,
            )
        )
    ).scalars().all()
    return {row.field_name: row.confirmed_value for row in rows}


async def confirm_sku_diff(
    session: AsyncSession,
    diff: IntegrationDiff,
    *,
    resolution: str,
    note: str | None,
    operator_id: int | None,
    now: datetime | None = None,
) -> dict:
    """核定一条 SKU 主数据差异。**只有这里能改 `skus` 的关键字段。**

    写回的范围严格受 `MASTER_FIELDS` 白名单约束；每次写入都会生成一版
    `sku_master_versions` 快照，所以"正式报价用过的口径"事后可回溯。
    """
    resolution = (resolution or "").strip()
    _validate_resolution(diff, resolution, note)
    moment = now or _now()
    applied = False
    version: SkuMasterVersion | None = None
    detail: dict[str, Any] = {}

    if diff.internal_id is None and resolution != RESOLUTION_MANUAL:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "这条差异还没有对应的本地 SKU（待匹配）：先补齐/指定 SKU 再核定，"
            "否则核定动作无处落地",
            422,
        )

    if diff.internal_id is not None:
        # 锁住 SKU 行：两个并发确认会同时算 version_no，靠行锁 + 唯一约束才能只出一版。
        sku = (
            await session.execute(
                select(Sku).where(Sku.id == diff.internal_id).with_for_update()
            )
        ).scalars().first()
        if sku is None:
            raise AppError(ErrorCode.NOT_FOUND, f"SKU #{diff.internal_id} 不存在", 404)

        if resolution == RESOLUTION_KEEP_LOCAL:
            if diff.field_name:
                # 差异核定不传 `dedupe`：它本身是一次有依据的人工裁定，
                # 即使值恰好与上一次相同，也算一次确认动作。
                await _confirm_field(
                    session,
                    sku=sku,
                    field_name=diff.field_name,
                    # "以本地为准"确认的是**当前本地值**：这就是人工确认版本的内容。
                    value=local_value(sku, diff.field_name),
                    operator_id=operator_id,
                    now=moment,
                )
            version = await _maybe_snapshot(
                session, sku=sku, operator_id=operator_id, note=note,
                diff_key=diff.diff_key, now=moment,
            )
            detail["confirmed"] = "本地值"
        elif resolution == RESOLUTION_TAKE_EXTERNAL:
            if not diff.field_name:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    "这条差异没有指明字段，无法执行「以来源为准」",
                    422,
                )
            incoming = normalize_field_value(diff.field_name, diff.incoming_value)
            row, _changed = await _confirm_field(
                session,
                sku=sku,
                field_name=diff.field_name,
                value=incoming,
                operator_id=operator_id,
                now=moment,
            )
            # 白名单 + 类型归一之后才写；写入的是**确认值**而不是来源原样。
            setattr(sku, diff.field_name, None if incoming is None else incoming)
            await session.flush()
            version = await _maybe_snapshot(
                session, sku=sku, operator_id=operator_id, note=note,
                diff_key=diff.diff_key, now=moment,
            )
            applied = True
            detail = {"field": diff.field_name, "value": _json_value(incoming), "version_no": row.confirmed_version}
        elif resolution == RESOLUTION_RENAME_LOCAL:
            new_code = _text(diff.incoming_value)
            if not new_code:
                raise AppError(ErrorCode.PARAM_ERROR, "差异里没有新编码，无法改码", 422)
            clash = (
                await session.execute(
                    select(Sku).where(Sku.sku_code == new_code, Sku.id != sku.id)
                )
            ).scalars().first()
            if clash is not None:
                raise AppError(
                    ErrorCode.DUPLICATE,
                    f"编码 {new_code} 已被 SKU #{clash.id} 占用：先合并/改名，不要覆盖",
                    409,
                )
            old_code = sku.sku_code
            sku.sku_code = new_code
            await session.flush()
            applied = True
            detail = {"old_code": old_code, "new_code": new_code}
        elif resolution == RESOLUTION_DISABLE_LOCAL:
            sku.status = "disabled"
            await session.flush()
            applied = True
            detail = {"status": sku.status}
        else:  # manual：人工已处理，不动主数据也不动确认版本
            detail = {"note": "人工处理，未改主数据"}

    diff.status = DIFF_IGNORED if resolution == RESOLUTION_IGNORE else DIFF_RESOLVED
    diff.resolution = resolution
    diff.resolve_note = note
    diff.resolved_by = operator_id
    diff.resolved_at = moment
    await session.flush()

    return {
        "diff": serialize_sku_diff(diff),
        "resolution_label": RESOLUTION_LABELS.get(resolution, resolution),
        "applied_to_local": applied,
        "detail": detail,
        "version": serialize_version(version) if version is not None else None,
        "message": (
            f"已记录核定结论：{RESOLUTION_LABELS.get(resolution, resolution)}"
            + (
                f"；已生成主数据版本 v{version.version_no}（正式报价可回溯到它）"
                if version is not None
                else ""
            )
        ),
    }


# ---------------------------------------------------------------- 查询与"正式报价入口"


def serialize_sku_diff(row: IntegrationDiff) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "sku_id": row.internal_id,
        "external_id": row.external_id,
        "field_name": row.field_name,
        "field_label": MASTER_FIELDS.get(row.field_name or "", row.field_name),
        "diff_type": row.diff_type,
        "current_value": row.current_value,
        "incoming_value": row.incoming_value,
        "status": row.status,
        "resolution": row.resolution,
        "resolution_label": RESOLUTION_LABELS.get(row.resolution or "", row.resolution),
        "resolve_note": row.resolve_note,
        "resolved_by": row.resolved_by,
        "resolved_at": row.resolved_at,
        "allowed_resolutions": list(
            ALLOWED_RESOLUTIONS.get(row.diff_type, (RESOLUTION_MANUAL,))
        ),
        "requires_note": row.diff_type in REQUIRE_NOTE,
        "evidence": row.evidence,
        "created_at": row.created_at,
    }


async def list_sku_diffs(
    session: AsyncSession,
    *,
    sku_id: int | None = None,
    status: str | None = None,
    diff_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt: Select = select(IntegrationDiff).where(IntegrationDiff.domain == DIFF_DOMAIN_SKU)
    if sku_id is not None:
        stmt = stmt.where(IntegrationDiff.internal_id == sku_id)
    if status:
        stmt = stmt.where(IntegrationDiff.status == status)
    if diff_type:
        stmt = stmt.where(IntegrationDiff.diff_type == diff_type)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(IntegrationDiff.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_sku_diff(row) for row in rows], total


async def list_identity_sources(
    session: AsyncSession,
    *,
    sku_id: int | None = None,
    system_type: str | None = None,
    match_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt: Select = select(SkuIdentitySource)
    if sku_id is not None:
        stmt = stmt.where(SkuIdentitySource.sku_id == sku_id)
    if system_type:
        stmt = stmt.where(SkuIdentitySource.system_type == system_type)
    if match_status:
        stmt = stmt.where(SkuIdentitySource.match_status == match_status)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(SkuIdentitySource.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_identity(row) for row in rows], total


async def get_identity_source(session: AsyncSession, source_id: int) -> SkuIdentitySource:
    row = await session.get(SkuIdentitySource, source_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU 外部身份 #{source_id} 不存在", 404)
    return row


async def sku_master_overview(session: AsyncSession, sku_id: int) -> dict:
    """SKU 的主数据总览：每个关键字段的**来源 / 更新时间 / 外部身份 / 确认版本**。

    这是前端"来源/待核实/差异"显示的唯一数据来源：未核实的来源在这里是
    `source_status = 待核实`，未拍板的字段权威是 `未拍板`，
    有待确认差异的字段是 `pending_confirmation`。
    """
    sku = await get_sku_or_404(session, sku_id)
    rows = (
        await session.execute(
            select(SkuFieldAuthority).where(SkuFieldAuthority.sku_id == sku.id)
        )
    ).scalars().all()
    by_field = {row.field_name: row for row in rows}
    fields = []
    for field, label in MASTER_FIELDS.items():
        row = by_field.get(field)
        local = local_value(sku, field)
        confirmed = row.confirmed_value if row else None
        has_confirmed = bool(row and (row.confirmed_version or 0) > 0)
        # 「本地值已修改、尚未重新确认」：本地值与已确认值不一致（issue 建议第 4 条）。
        # 判据用**字符串比较**并显式区分 None 与 ""：
        #   - 从没确认过（has_confirmed=False）不算"已修改"，那是"未确认"；
        #   - 确认值 `""`（合法的空规格）与本地 `None` 要不要算不一致？
        #     算 —— 它们是不同的值，"确认了空、现在填了东西"必须提示。
        _lv = "" if local is None else str(local)
        _cv = "" if confirmed is None else str(confirmed)
        differs = has_confirmed and (_lv != _cv)
        fields.append(
            {
                "field_name": field,
                "field_label": label,
                "local_value": local,
                "source_system": row.source_system if row else None,
                "source_verified": bool(row.source_verified) if row else False,
                # 「本地字段已确认」与「外部来源已核实」是**两件事**（第 6 条）。
                # 没有外部来源时显示「无外部来源」，不挂一个长期误导的「待核实」——
                # 本地自建的 SKU 本来就没有外部来源，写「待核实」会让人以为
                # "还差一步外部核对"，而它永远等不到。
                "source_status": (
                    "已核实"
                    if (row and row.source_verified)
                    else ("待核实" if (row and row.source_system) else "无外部来源")
                ),
                "external_identity": row.external_identity if row else None,
                "source_updated_at": row.source_updated_at if row else None,
                "source_value": row.source_value if row else None,
                "authority": row.authority if row else None,
                "authority_label": (row.authority if row and row.authority else "未拍板"),
                "confirmed_version": row.confirmed_version if row else 0,
                "confirmed_value": confirmed,
                #: 本地值是否与已确认值不同 —— 前端据此提示「本地值已修改，尚未重新确认」
                "local_differs_from_confirmed": differs,
                "confirmed_at": row.confirmed_at if row else None,
                "status": row.status if row else AUTH_UNVERIFIED,
            }
        )
    pending, pending_total = await list_sku_diffs(
        session, sku_id=sku.id, status=DIFF_OPEN, page=1, page_size=50
    )
    versions = (
        await session.execute(
            select(SkuMasterVersion)
            .where(SkuMasterVersion.sku_id == sku.id)
            .order_by(SkuMasterVersion.version_no.desc())
            .limit(5)
        )
    ).scalars().all()
    identities = (
        await session.execute(
            select(SkuIdentitySource)
            .where(SkuIdentitySource.sku_id == sku.id)
            .order_by(SkuIdentitySource.id.asc())
        )
    ).scalars().all()
    return {
        "sku_id": sku.id,
        "sku_code": sku.sku_code,
        "sku_status": sku.status,
        "fields": fields,
        "pending_diffs": pending,
        "pending_diff_count": pending_total,
        "recent_versions": [serialize_version(row) for row in versions],
        "latest_confirmed_version": versions[0].version_no if versions else 0,
        "identities": [serialize_identity(row) for row in identities],
        "notes": [
            "来源未核实的一律显示「待核实」，不默认任一系统为主",
            "字段权威为「未拍板」表示还没有人明确过归属",
            "外部值不会自动写回本地 SKU；改口径要走差异核定并生成新的确认版本",
        ],
    }


async def confirmed_master_version(session: AsyncSession, sku_id: int) -> dict | None:
    """这个 SKU 最近一版**已确认**的主数据（正式报价应当引用它）。"""
    row = (
        await session.execute(
            select(SkuMasterVersion)
            .where(SkuMasterVersion.sku_id == sku_id)
            .order_by(SkuMasterVersion.version_no.desc())
        )
    ).scalars().first()
    return serialize_version(row) if row is not None else None


async def require_confirmed_master(
    session: AsyncSession, sku_id: int, fields: list[str] | None = None
) -> dict:
    """正式报价前的闸门：要求这些字段都有**已确认**的版本，否则明确报缺哪些。

    为什么要这个函数：§8.14 要求"正式报价仅用已确认的主数据版本"。
    报价模块（`quote/`）不在本轮改动范围内，所以这里提供**可直接调用的闸门**，
    由报价侧接线；在此之前，未确认的字段在接口上一律是 `unverified`，
    没有任何地方会把来源值当成已确认值使用。
    """
    wanted = list(fields or MASTER_FIELDS)
    unknown = [field for field in wanted if field not in MASTER_FIELDS]
    if unknown:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不是 SKU 关键字段：{'、'.join(unknown)}",
            422,
        )
    await get_sku_or_404(session, sku_id)
    rows = (
        await session.execute(
            select(SkuFieldAuthority).where(
                SkuFieldAuthority.sku_id == sku_id,
                SkuFieldAuthority.field_name.in_(wanted),
                SkuFieldAuthority.confirmed_version > 0,
            )
        )
    ).scalars().all()
    confirmed = {row.field_name: row.confirmed_value for row in rows}
    missing = [field for field in wanted if field not in confirmed]
    if missing:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "以下字段还没有人工确认的主数据版本，不能用于正式报价："
            + "、".join(f"{field}（{MASTER_FIELDS[field]}）" for field in missing),
            409,
        )
    version = await confirmed_master_version(session, sku_id)
    return {
        "sku_id": sku_id,
        "values": confirmed,
        "version": version,
        "message": "字段均已确认，可用于正式报价",
    }


async def resolve_confirmed_master(
    session: AsyncSession, sku_id: int, fields: list[str] | None = None
) -> dict:
    """报价流程用的**不阻断**版本：解析这个 SKU 的已确认主数据（§8.14 接线）。

    口径（2026-10-07 确认）：**默认放行 + 如实提示**。

    为什么不做成硬闸门：字段权威表整张还是空的（业务还没拍板"哪个字段以谁为准"），
    要求"没有已确认版本就不许报价"等于**把所有报价堵死**。
    但也不能不接 —— `require_confirmed_master` 写了却零调用，
    "正式报价只用已确认的主数据"就只是注释里的一句话。

    实现上**判据只有一份**：内部照常调 `require_confirmed_master`，
    只把"缺确认"这一档（40002 + 那句"不能用于正式报价"）降级为
    "能拿到多少就用多少，缺的列出来提示"。另抄一份判定逻辑迟早会和闸门漂移，
    那正是"看着接了线、其实没生效"的来源。
    其它错误（字段名非法、SKU 不存在）照抛，不吞。
    """
    wanted = list(fields or MASTER_FIELDS)
    try:
        gate = await require_confirmed_master(session, sku_id, wanted)
        confirmed = gate["values"]
        missing: list[str] = []
        version = gate["version"]
    except AppError as exc:
        if exc.code != ErrorCode.STATUS_NOT_ALLOWED:
            raise
        # 闸门因"有字段未确认"拒绝：这里不阻断，改为能拿多少用多少。
        rows = (
            await session.execute(
                select(SkuFieldAuthority).where(
                    SkuFieldAuthority.sku_id == sku_id,
                    SkuFieldAuthority.field_name.in_(wanted),
                    SkuFieldAuthority.confirmed_version > 0,
                )
            )
        ).scalars().all()
        confirmed = {row.field_name: row.confirmed_value for row in rows}
        missing = [field for field in wanted if field not in confirmed]
        version = await confirmed_master_version(session, sku_id)
    return {
        "sku_id": sku_id,
        "values": confirmed,
        "unconfirmed": missing,
        "unconfirmed_labels": [MASTER_FIELDS[field] for field in missing],
        # 版本号必须指向**真正的整版快照**（`sku_master_versions.version_no`）。
        # 上一版这里取"字段确认次数里最大的那个"—— 两套编号来源完全不同，
        # 实测：确认名称 → 整版 V1；再确认单位 → 整版 V2；此时字段确认次数
        # 最大值仍是 1，于是明细记成 1，**按这个号回查整版 V1 里根本没有单位**。
        # 详见 `quoted_master_version_no`。
        "version_no": await quoted_master_version_no(session, sku_id, confirmed),
        "version": version,
    }


async def quoted_snapshot_problems(
    session: AsyncSession,
    *,
    sku_id: int,
    version_no: int | None,
    fields: tuple[str, ...] = QUOTE_DISPLAY_FIELDS,
    actual: dict[str, str | None] | None = None,
) -> list[str]:
    """明细引用的那一版主数据快照，**够不够印给客户**（返回有问题的字段中文名）。

    判据是"回查 `quote_items.master_version_no` 指向的那一版
    `sku_master_versions`，看它里面有没有这些字段"，而不是"现在这个 SKU
    确认了没有"。差别很实在：

    - 事后补确认**不能**给旧明细洗白 —— 旧明细的版本号指向旧快照，
      而那份快照里确实没有该字段，那一版对客文件用的就是没确认的值；
    - 也回答得了审查的那句"校验明细引用的**真实主数据快照**及其字段确认完整性"。

    `version_no` 为 None（生成明细时还没有可引用的快照）时返回一条明确的说明。

    **`actual`（这一版明细实际要印给客户的值）传进来时，还要逐字段比对
    "明细实际值 == 所引用快照的值"** —— §8.14 复审（第四轮）补的第三个待办。
    只查"键在不在"挡不住下面这条链：

        确认名称/规格/单位，其中规格**确认为空** → 整版 V3（specification: ""）
        → 本地 SKU 后填一个"尚未确认的新规格"
        → 生成明细被 `or` 回退成那个新规格，而 master_version_no 仍指着 V3
        → 只查键：V3 里有 specification → 放行 ✗

    比对按**字符串**做：确认值是 `""`（合法的空规格）时明细也必须是 `""`，
    明细填了别的东西就对不上 → 报不一致。依据始终是明细记下的那一版，
    不拿今天的 SKU 值替代 —— 否则"事后改 SKU"就能把旧明细洗白。
    """
    if version_no is None:
        return ["（没有可引用的已确认主数据版本）"]
    snapshot = (
        await session.execute(
            select(SkuMasterVersion).where(
                SkuMasterVersion.sku_id == sku_id,
                SkuMasterVersion.version_no == version_no,
            )
        )
    ).scalars().first()
    values = (snapshot.values if snapshot is not None else None) or {}
    problems: list[str] = []
    for field in fields:
        # 判据是"**这个字段有没有确认值**"，不是"确认值非空"：
        # 快照里根本没有这个键 = 从没确认过 → 缺；
        # 有键但值是空串 = 确认过、而这个 SKU 的该字段本来就是空（例如没有规格的产品），
        # 不该因为"确认为空"就挡住正式报价（那会把合法业务也拦下来）。
        if field not in values:
            problems.append(MASTER_FIELDS[field])
            continue
        if actual is None:
            continue
        if str(values[field] or "") != str(actual.get(field) or ""):
            # 值对不上：把"差在哪"一起说出来，否则用户只看到「规格」两个字、
            # 不知道究竟要改什么。空值写成「空」，避免看起来像没渲染。
            problems.append(
                f"{MASTER_FIELDS[field]}（明细用的是「{actual.get(field) or '空'}」，"
                f"与所引用确认版本的「{values[field] or '空'}」不一致）"
            )
    return problems


async def quoted_master_version_no(
    session: AsyncSession, sku_id: int, confirmed: dict[str, str]
) -> int:
    """报价明细应当引用的**整版主数据快照**编号（0 = 没有可引用的快照）。

    §8.14 复审（前三轮修完还剩的第二个待办）：上一版取的是
    "已确认字段各自 `confirmed_version` 的最大值"，而整版快照有**另一套编号**
    （`sku_master_versions.version_no`，每次确认动作新出一版）。两套数不能互替：

        确认名称 → 整版 V1（values 只有 name）
        再确认单位 → 整版 V2（values 有 name + unit）
        新明细：unit 用的是已确认的"件"，`master_version_no` 却记成 1
        回查整版 V1 → 里面没有 unit

    于是"按保存的版本号回溯实际来源"这件事做不到。现在改成取**最新整版**，
    并要求该版**确实包含明细采用的每个字段值**；对不上就返回 0
    （如实表示"没有可引用的快照"），**不拼一个看起来有效的号**。

    没有确认字段、或该 SKU 还没有任何整版快照时同样返回 0 ——
    调用方把它落成 `NULL`，语义是"当时还没有可引用的已确认主数据"。
    """
    if not confirmed:
        return 0
    row = (
        await session.execute(
            select(SkuMasterVersion)
            .where(SkuMasterVersion.sku_id == sku_id)
            .order_by(SkuMasterVersion.version_no.desc())
        )
    ).scalars().first()
    if row is None:
        return 0
    values = row.values or {}
    for field, value in confirmed.items():
        if str(values.get(field) or "") != str(value or ""):
            return 0
    return int(row.version_no)


__all__ = [
    "ALLOWED_RESOLUTIONS",
    "MASTER_FIELDS",
    "PACKAGE_FIELDS",
    "REQUIRE_NOTE",
    "RESOLUTION_LABELS",
    "UNIT_FIELDS",
    "WRITING_RESOLUTIONS",
    "build_sku_diff_key",
    "confirm_local_master",
    "quote_master_refresh_preview",
    "confirm_sku_diff",
    "confirmed_master_version",
    # §8.14 复审补的：整版快照号 + "那一版够不够印给客户"的判据
    "quoted_master_version_no",
    "quoted_snapshot_problems",
    "QUOTE_DISPLAY_FIELDS",
    "get_identity_source",
    "get_sku_or_404",
    "ingest_external_sku",
    "list_identity_sources",
    "list_sku_diffs",
    "local_value",
    "mark_identity_stopped",
    "normalize_field_value",
    "rename_identity_source",
    "replay_identity_source",
    "require_confirmed_master",
    "resolve_confirmed_master",
    "serialize_identity",
    "serialize_sku_diff",
    "serialize_version",
    "set_field_authority",
    "sku_master_overview",
]


# ---------------------------------------------------------------- 报价刷新预览


def _norm_text(value: Any) -> str:
    return "" if value is None else str(value)


async def quote_master_refresh_preview(
    session: AsyncSession,
    *,
    version,  # QuoteVersion（不 import 以免循环依赖）
    items: list,  # list[QuoteItem]
) -> dict:
    """报价草稿刷新主数据前**先给人看会变什么**（issue 建议第 5 条）。

    为什么要有预览：刷新会把明细上的名称/规格/单位换成最新已确认快照，
    而这三个字段是**印给客户**的。让人先看清"从什么变成什么"再确认，
    比刷完再发现印错了便宜得多。

    返回每条明细的逐字段变化 + 一个总的 `changed_count`。
    只做读，不写任何东西 —— 真正的刷新走既有的 `/quote-versions/{id}/price-refresh`
    （它已经在同一个事务里按已确认主数据重建快照）。

    已发送的版本不给刷：报价发出去之后内容就是**对客承诺**，
    改它等于改承诺。这条路只能新建版本（由调用方判断并提示）。
    """
    rows: list[dict] = []
    changed_total = 0
    never_referenced = 0
    for item in items:
        code = item.sku_code_snapshot or f"SKU#{item.sku_id}"
        if item.sku_id is None:
            # 定制项没有 SKU 主数据，跳过（它的名称是人工填的，不属于主数据口径）
            continue
        master = await resolve_confirmed_master(session, item.sku_id)
        confirmed = master.get("values") or {}
        # ⚠️ **从未引用过任何已确认版本**的明细（`master_version_no` 为空）必须说清楚。
        # 它生成时还没有可引用的快照，所以"明细当前值"未必等于任何已确认值 ——
        # 只按"明细值 vs 最新已确认值"比，会在两者恰好相同时报"无需刷新"，
        # 而那正是最需要刷新的一条（刷新才会把版本号钉上去）。
        # 实测踩到：明细的 name/spec/unit 快照全为空、master_version_no 为空，
        # 预览却说"都引用最新、刷新不会改变内容" —— 误导。
        if item.master_version_no is None:
            never_referenced += 1
        fields: list[dict] = []
        for field in QUOTE_DISPLAY_FIELDS:
            current = _norm_text(
                {
                    "name": item.sku_name_snapshot,
                    "specification": item.spec_snapshot,
                    "unit": getattr(item, "unit_snapshot", None),
                }.get(field)
            )
            incoming = _norm_text(confirmed.get(field))
            if current != incoming:
                fields.append(
                    {
                        "field": field,
                        "label": MASTER_FIELDS[field],
                        "before": current or None,
                        "after": incoming or None,
                    }
                )
        if fields or item.master_version_no is None:
            changed_total += 1
        rows.append(
            {
                "item_id": item.id,
                "sku_id": item.sku_id,
                "sku_code": code,
                # 明细当前引用的主数据版本（空 = 生成时还没有可引用的已确认版本）
                "master_version_no": item.master_version_no,
                "never_referenced": item.master_version_no is None,
                "confirmed_version_no": (master.get("version") or {}).get("version_no"),
                "unconfirmed": master.get("unconfirmed") or [],
                "unconfirmed_labels": master.get("unconfirmed_labels") or [],
                "changes": fields,
            }
        )
    if not rows:
        # 空版本（一条明细都没有）：不能让文案说"都已引用最新" ——
        # 那听起来像"检查过了、没问题"，实际是"没什么可检查的"。
        _msg = (
            "这一版没有明细（没有 SKU 行），刷新不会改变任何内容"
            if not items
            else "这一版只有定制项，没有引用 SKU 主数据的明细"
        )
    elif changed_total:
        _msg = f"{changed_total} 条明细会因刷新而改变"
        if never_referenced:
            _msg += f"（其中 {never_referenced} 条还没有引用过任何已确认版本）"
    else:
        _msg = "所有明细都已引用最新的已确认主数据，刷新不会改变内容"
    return {
        "items": rows,
        "changed_count": changed_total,
        "never_referenced_count": never_referenced,
        "item_count": len(rows),
        "display_fields": list(QUOTE_DISPLAY_FIELDS),
        "display_field_labels": [MASTER_FIELDS[f] for f in QUOTE_DISPLAY_FIELDS],
        "message": _msg,
    }
