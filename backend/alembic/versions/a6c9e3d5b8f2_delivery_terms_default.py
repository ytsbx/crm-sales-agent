"""交货条款默认文案跟着运费口径一起改（2026-10-09「产品价格与运费分离」）。

## 为什么必须单独做一条数据迁移

新报价的默认交货条款在代码里已经改成：

    产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付。

但 `settings_service.DEFAULT_SETTINGS` 只是**没有库记录时的回退值**。一旦
`system_settings` 里有 `default_delivery_terms` 这一行（部署后第一次读配置、
或 seed 时就写进去了），库里那份就**永久生效**，代码里改默认值不起作用 ——
于是"新建报价"的条款还是老文案「含运费，送货上门」。

老文案在新口径下是**会对客户说错话**的默认值：产品单价已经不含运费了，
继续写"含运费"会让客户以为报价里包了运费。

## 只改"还是老默认值"的行

判据是"这一行的值**逐字等于**老默认值"。理由：

- 等于老默认值 → 说明业务从没改过它，是"默认值跟着代码走"的情形，该更新；
- 不等于（哪怕只差一个字）→ 业务**明确配过**自己的条款，一律不动，
  更不能拿它去覆盖成我们新写的这句。

`value` 是 JSONB，形状是 `{"text": "..."}`；这里用 JSONB 的 `-»` 取值比较，
避免把 JSON 当成字符串误判。

## 与前一条迁移的关系

`e4a7c1b9f2d6`（报价运费分离）改的是报价版本与费用行的列；这一条只动一行配置。
两条都不触碰历史报价版本的 `delivery_terms` 快照 —— 那些是当时发出去的事实。
"""
from alembic import op

revision = "a6c9e3d5b8f2"
down_revision = "f5b8d2c4a7e1"
branch_labels = None
depends_on = None

#: 老默认值（会被替换掉的那个）
OLD_TEXT = "含运费，送货上门"
#: 新默认值（与 `settings_service.DEFAULT_SETTINGS` 和
#: `quote.service.create_quote` 的代码回退值**逐字一致**，三处必须同步）
NEW_TEXT = "产品单价不含运费。运费单列，按已确认的实际金额由本公司代收代付。"


def upgrade() -> None:
    op.execute(
        """
        UPDATE system_settings
        SET value = jsonb_build_object('text', %(new)s::text)
        WHERE key = 'default_delivery_terms'
          AND value ->> 'text' = %(old)s
        """
        % {"new": _lit(NEW_TEXT), "old": _lit(OLD_TEXT)}
    )


def downgrade() -> None:
    # 反向只还原"正好是新默认值"的行，业务自己配过的一律不动。
    op.execute(
        """
        UPDATE system_settings
        SET value = jsonb_build_object('text', %(old)s::text)
        WHERE key = 'default_delivery_terms'
          AND value ->> 'text' = %(new)s
        """
        % {"new": _lit(NEW_TEXT), "old": _lit(OLD_TEXT)}
    )


def _lit(text: str) -> str:
    """把文本转成 SQL 字面量（转义单引号）。中文里可能有引号，别手写拼接。"""
    return "'" + text.replace("'", "''") + "'"
