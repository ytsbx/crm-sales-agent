"""业务时间的唯一基准（第九批 §9.10）。

## 为什么需要这个模块

代码里此前**混用四种时间基准**（审查实测）：

- `datetime.now(UTC)`（UTC 瞬时，写库用，本身没问题）；
- `datetime.astimezone()` / `date.today()`（跟着**宿主机时区**走）；
- 数据库会话时区（由部署容器的 TZ 决定，`extract('month', col)` 用的是它）；
- 调度器明确指定的 `Asia/Shanghai`。

后果的实例：北京时间 `2026-01-01 01:00` 的订单，库里存 UTC 是
`2025-12-31 17:00`，按 UTC 年边界归期就被算进**上一年**；
宿主机时区若是 UTC，同一个报表还会再变一次。

## 口径（2026-10-07 业务已拍板）

**本阶段统一按北京时间归期。**

- 存储不变：`timestamptz` 继续存 UTC 瞬时，不迁移、不改列类型；
- 判断统一：年 / 月 / "今天"这些**业务口径**一律先换算到北京时间再看；
- 历史不改：已冻结的报表不因这次修复静默重算（要走"带原因的重算"流程）。

将来有海外团队时，把 `BUSINESS_TZ` 换成配置项即可 —— 这也是把基准收进
一个模块而不是散在各处的原因。
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import literal_column

#: 业务时区。当前公司只按国内时间运营（2026-10-07 拍板）。
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")

#: 给 SQL 用的时区名 —— PostgreSQL 的 `timezone('Asia/Shanghai', col)` 要字符串。
BUSINESS_TZ_NAME = "Asia/Shanghai"

#: 上面那个名字的 **SQL 字面量**形式（见 `business_month` 的说明：用绑定参数
#: 会让 GROUP BY 匹配不上）。
_BUSINESS_TZ_SQL = literal_column(f"'{BUSINESS_TZ_NAME}'")


def now_business() -> datetime:
    """业务时区的"现在"（带时区信息）。"""
    return datetime.now(BUSINESS_TZ)


def today_business() -> date:
    """业务时区的"今天"。

    替代 `date.today()`（跟宿主机走）与 `datetime.now(UTC).date()`
    （北京时间凌晨 0-8 点会算成前一天）。
    """
    return now_business().date()


def to_business(value: datetime) -> datetime:
    """把一个时间换算到业务时区。naive 值按 UTC 解释（库里的列都是 timestamptz）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).astimezone(BUSINESS_TZ)
    return value.astimezone(BUSINESS_TZ)


def month_key(value: datetime | None) -> str | None:
    """业务时区下的 `YYYY-MM`（归月口径的唯一实现）。"""
    if value is None:
        return None
    return to_business(value).strftime("%Y-%m")


def business_day_start(year: int, month: int = 1, day: int = 1) -> datetime:
    """业务时区某天 00:00 对应的 **UTC 瞬时**（可以直接和库里的列比较）。"""
    return datetime(year, month, day, tzinfo=BUSINESS_TZ).astimezone(UTC)


def year_bounds(year: int) -> tuple[datetime, datetime]:
    """业务时区下的 [年初, 次年初) —— 统计归年统一用它。

    原来这里是 `datetime(y, 1, 1, tzinfo=UTC)`：跨年那 8 小时的单子会归错年。
    """
    return business_day_start(year), business_day_start(year + 1)


def business_month(column):
    """SQL 侧归月：`extract('month', col)` 走的是**数据库会话时区**
    （部署时由容器 TZ 决定），换个环境同一个报表就会变。这里显式指定业务时区。

    ⚠️ **只用于 `timestamptz` 列**（`created_at` / `confirmed_at` 这类）。
    对 `date` 列（如 `actual_ship_date`）不要用 —— date 本来就没有时区概念，
    套上 `timezone()` 反而会被当成当地时间再换算一次，可能整月偏移。

    ⚠️ 时区名必须是 **SQL 字面量**，不能走绑定参数：写成 `func.timezone("Asia/Shanghai", col)`
    时 SQLAlchemy 会渲染成 `timezone($1, col)`，那样 SELECT 与 `GROUP BY` 里的
    表达式**在 PG 眼里不是同一个**（参数节点不相等），于是报
    「column must appear in the GROUP BY clause」。用 `literal_column` 让它
    以同样的字面文本出现在两处。
    """
    from sqlalchemy import func

    return func.extract("month", func.timezone(_BUSINESS_TZ_SQL, column))


def business_year(column):
    """SQL 侧归年，同 `business_month`（同样只用于 timestamptz 列）。"""
    from sqlalchemy import func

    return func.extract("year", func.timezone(_BUSINESS_TZ_SQL, column))
