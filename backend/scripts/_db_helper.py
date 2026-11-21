"""给回归套件用的**同步** SQL 助手（psycopg2，不发 SQLAlchemy 事件循环）。

## 为什么需要它

多数套件是纯 `async def main()`，直接 `await session.execute(...)` 就行。
但 `check_c3_round_repairs` / `check_c4_round_repairs` 的断言散在**同步代码**里，
中间还夹着 `async def _run()` —— 这时候：

- `asyncio.run()` 在 async 上下文里会报 "cannot be called from a running event loop"；
- 换个专属事件循环线程去跑 `app.core.database` 的**异步** engine 也不行：
  asyncpg 的连接绑在**第一次用它的事件循环**上，而那台 engine 早被套件自己的
  `asyncio.run()` 绑走了 → `RuntimeError: got Future attached to a different loop`
  （实测踩到）。

所以要一个**跟事件循环完全无关**的通道：同步驱动 psycopg2。

## 从前是怎么做的（两个坑）

原来用 `subprocess` 调 **psql**：

1. **依赖外部二进制**：`PSQL_BIN` 默认写死 macOS 的
   `/opt/homebrew/opt/postgresql@15/bin/psql`，Ubuntu（CI）上不存在 →
   `subprocess.run` 抛 `FileNotFoundError`。清单里另外 91 个套件**没有一个**
   依赖 psql，这是唯一的例外（2026-10-11 CI 实测踩到）。
2. **库名两个真源**：`psql` 用 `-d DB`（吃 `DB_NAME`），夹具用 `DATABASE_URL`；
   CI 只设后者，两边就会指向不同的库。

现在统一从 `DATABASE_URL` 取连接串，不再依赖任何外部命令，也不再有第二个库名来源。

## 返回值

照搬 `psql -tAc` 的语义，好让 60 多处调用点一行不用改：

- 单行单列 → 该值的字符串（`None` → `""`）；
- 多行单列 → 用 `\\n` 连接；
- 多行多列 → 每行用 `|` 连接、行间用 `\\n`（没有调用点依赖这个格式，
  留着只是为了排查时能看）；
- 出错 → `"SQLERR:" + 一句有用的话`（**不抛异常**，与旧行为一致 —— 调用点普遍是
  `not x.startswith("SQLERR")` 这么判的）。

⚠️ 因为出错**不抛异常**，写操作被外键挡回来会**静默失败**。所以清理清单必须按
依赖顺序排全，别指望它报错（C4 套件实测连撞四次才发现漏清）。
"""

from __future__ import annotations

import os
import threading
from decimal import Decimal

import psycopg2

_conn = None
_dsn: str | None = None
_lock = threading.Lock()


def _current_dsn() -> str:
    """从 `DATABASE_URL` 取连接串，去掉 SQLAlchemy 的 `+asyncpg` 驱动后缀。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库）。"
            "库名规则见 scripts/_test_support.require_isolated_db。"
        )
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _get_conn():
    """懒建连接；`DATABASE_URL` 换了就重连（套件换库时不会串）。"""
    global _conn, _dsn
    dsn = _current_dsn()
    with _lock:
        if _conn is None or _conn.closed or _dsn != dsn:
            if _conn is not None and not _conn.closed:
                try:
                    _conn.close()
                except Exception:  # noqa: BLE001 —— 关旧连接失败不该挡住重连
                    pass
            _conn = psycopg2.connect(dsn)
            _conn.autocommit = True
            _dsn = dsn
    return _conn


def db(sql: str) -> str:
    """同步执行一条 SQL，返回 `psql -tAc` 同形的字符串；出错返回 `SQLERR:...`。"""
    try:
        with _get_conn().cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                return ""
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 —— 与旧行为一致：不抛，转成 SQLERR
        return "SQLERR:" + _brief(exc)
    if not rows:
        return ""
    if len(rows[0]) == 1:
        return "\n".join(_cell(r[0]) for r in rows)
    return "\n".join("|".join(_cell(c) for c in r) for r in rows)


def _cell(value) -> str:
    """把一个值渲染成 **`psql -tAc` 的样子**。

    断言是按 psql 的输出写的（例如 `select x is not null` 期望 `"t"`），
    所以格式必须对齐，不能直接用 Python 的 `str()`：

    - 布尔 → `t` / `f`（Python 会给 `True` / `False`）；
    - NULL → 空串（不是 `None`）；
    - 日期/时间 → `isoformat()`（与 psql 一致）；
    - 数组 → `{a,b}`（Python 的 list `str()` 是 `[1, 2]`，带空格）。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, Decimal):
        # 数值列请尽量在 SQL 里 `::text`（两个套件都这么写）。
        # 这里兜一道：`Decimal.__str__` 对常规范围与 psql 同形，
        # 但**不写死 `'f'` 格式** —— 极大/极小值会被渲染成科学计数法，
        # 写死 'f' 会炸（InvalidOperation），那比格式不一致更糟。
        return str(value)
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(_cell(v) for v in value) + "}"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _brief(exc: BaseException) -> str:
    """取一句有用的报错。psycopg2 的多行 message 里第一行才是原话。"""
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    return (lines[0] if lines else type(exc).__name__)[:90]


__all__ = ["db"]
