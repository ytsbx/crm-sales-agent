"""给一次性隔离库里的 SKU 批量补上「主数据已确认」（验收 / UI 冒烟夹具用）。

## 为什么需要（2026-10-07）

正式发送报价前有一道**硬校验**：这版明细引用的主数据必须已经确认过
（`quote/service.ensure_items_master_confirmed`；口径 2026-10-07 确认 ——
**草稿随便建、正式发送时必须过**）。

而 UI 冒烟脚本用的是 seed 造出来的演示 SKU，**默认没有确认记录**（这是合理的：
新 SKU 本来就该等人去确认）。于是"自动建单"那一步的发送被拦：

    POST /quote-versions/{id}/mark-sent
    → 422 以下明细的 SKU 主数据尚未确认，不能正式发送：ZX-6040-B 缺 名称、规格、…

后面订单详情、客户接受/拒绝等一连串验收全部连带失败。
按项目规矩：**行为变更要改夹具，不是放宽规则** —— 所以给夹具补上确认记录。

## 第二轮收紧（2026-10-07，前八批遗留 §8.14-①）：还要落一版「整版快照」

复审把闸门口径又收紧了一档：不再看"这个 SKU 现在的字段确认状态"，而是**回查报价明细
自己记下的那一版快照**（`quote_items.master_version_no` → `sku_master_versions`），
检查**印给客户的三个字段**（名称/规格/单位）在那一版里在不在
（`quote/service.master_confirmation_problems` → `product/master.quoted_snapshot_problems`）。

于是只补 `sku_field_authorities` 不够了：明细生成时要能取到一个**整版号**
（`product/master.quoted_master_version_no` 只在确实存在、且逐字段值与确认值一致时
才返回非 0），否则明细落成 `master_version_no = NULL`，正式发送照旧被 422 拦下：

    POST /quote-versions/{id}/mark-sent
    → 422 以下明细的主数据尚未确认，不能正式发送：ZX-6040-B 缺 （没有可引用的已确认主数据版本）

所以本脚本现在**一次做两件事**：补字段确认 + 落一版整版快照。快照的 `values`
用与 `confirmed_value` **完全相同**的 `to_jsonb(s.<列>)` 构造，保证
`quoted_master_version_no` 的逐字段比对能对上（只造一份值、两处引用，避免表示形式漂移）。

## 只允许对一次性库跑

库名不含 test / iso / smoke 一律拒绝执行（与其它夹具脚本同一口径）：
这条 SQL 会把库里**所有未删除的 SKU** 都标成"已确认"，
要是误对开发库跑，等于替主人把所有产品的主数据都盖了章。

## 用法

    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/confirm_sku_master.py

幂等：已经有确认记录的字段不动（只补缺的）。
"""

import asyncio
from urllib.parse import urlparse

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.modules.product.master import MASTER_FIELDS


def ensure_isolated_db() -> str:
    """只允许对一次性隔离库跑，返回库名。"""
    name = (urlparse(settings.database_url).path or "").lstrip("/").lower()
    if not any(token in name for token in ("test", "iso", "smoke")):
        raise SystemExit(
            f"只允许对一次性隔离库跑（库名需含 test / iso / smoke），当前库名：{name or '(空)'}"
        )
    return name


async def main() -> None:
    db_name = ensure_isolated_db()
    inserted = 0
    async with engine.begin() as conn:
        for field in MASTER_FIELDS:
            # 列名来自 MASTER_FIELDS 这个白名单（字段名与 skus 表的列同名），不是外部输入
            # 同一个 :field 既当插入值又做等值比较，asyncpg 在 prepare 阶段会把它
            # 推断成两种类型（text / character varying）而报 AmbiguousParameterError ——
            # 所以两处都显式 CAST 成 text，参数类型就唯一了。
            result = await conn.execute(
                text(
                    f"""
                    insert into sku_field_authorities
                        (sku_id, field_name, source_verified, confirmed_version,
                         confirmed_value, status, created_at, updated_at)
                    select s.id, CAST(:field AS text), false, 1, to_jsonb(s.{field}),
                           'confirmed', now(), now()
                    from skus s
                    where s.deleted_at is null
                      and not exists (
                          select 1 from sku_field_authorities a
                          where a.sku_id = s.id and a.field_name = CAST(:field AS text)
                      )
                    """
                ),
                {"field": field},
            )
            inserted += result.rowcount or 0

        # 再落一版整版快照（version_no = 1）：报价明细的 master_version_no 指向的就是它。
        # 字段值与上面 confirmed_value 用同一个表达式（to_jsonb(s.<列>)），
        # 两边读出来完全相同，quoted_master_version_no 的比对才过得去。
        # 列名来自 MASTER_FIELDS 白名单，不是外部输入。
        snapshot_pairs = ", ".join(
            f"'{field}', to_jsonb(s.{field})" for field in MASTER_FIELDS
        )
        snapshots = await conn.execute(
            text(
                f"""
                insert into sku_master_versions
                    (sku_id, version_no, values, source_summary, confirmed_by,
                     confirmed_at, note, created_at, updated_at)
                select s.id, 1, jsonb_build_object({snapshot_pairs}),
                       '{{}}'::jsonb, NULL, now(),
                       '冒烟夹具：确认关键字段并落一版整版快照', now(), now()
                from skus s
                where s.deleted_at is null
                on conflict (sku_id, version_no) do update set
                    values = excluded.values, updated_at = now()
                """
            )
        )
        snapshot_count = snapshots.rowcount or 0
    await engine.dispose()
    print(
        f"库 {db_name}：已补 {inserted} 条字段确认"
        f"（{len(MASTER_FIELDS)} 个字段 × 未删除的 SKU，已有的不动），"
        f"并落整版快照 {snapshot_count} 版"
    )


if __name__ == "__main__":
    asyncio.run(main())
