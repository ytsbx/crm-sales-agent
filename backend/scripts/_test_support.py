"""套件之间共用的测试辅助（不是业务代码，不进任何接口路径）。

只放**多个套件真正重复、且写错一次就会全体踩坑**的东西：

1. `align_id_sequences` —— 把自增序列只向前推（见下）；
2. `require_api_base` / `require_isolated_db` —— **防呆**，见「防呆」一节。

## 防呆：测试一律不许打到开发后端 / 正式库（2026-10-08 加）

起因：套件从前直接写默认值
`os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")`，而 `backend/.env`
里的 `DATABASE_URL` 指向开发库 `crm_sales_agent`。于是**不显式指定就等于"在正式库上
跑测试"**：2026-09-28 ~ 10-06 之间，开发库里陆续留下一个探针角色（还挂在张三账号上）、
4 个 `chk_*` 测试账号、两条测试线索、一张已取消订单和一个复购商机。

现在规则只有这一处：

- `API_BASE` 必须**显式给**，不给就退出 —— 不许再用"默认 8000"；
- 地址不许指到 **8000**（开发后端），也不许不是本机；
- `DATABASE_URL` 必须**显式给**，且库名必须是**一次性库**
  （`crm_iso*` / `crm_check*` / `crm_test*` 开头，或 `_test` 结尾）。

⚠️ 关键点：这里读的是**环境变量**，不是 `app.core.config` 里的值。
`backend/.env` 的 `DATABASE_URL` 只会被 pydantic 读进 settings，**不会**进
`os.environ` —— 所以"`os.environ` 里没有 `DATABASE_URL`"恰好等价于
"调用方没有明确指定一个测试库"，正是要拦的那一种。也因此这两个函数必须在
`import app.*` **之前**调用：settings 在 import 时就决定了连哪个库。

真要拿开发库/开发后端临时验一次，设 `ALLOW_DEV_TARGETS=1`（明知故犯，会大声提醒）。

## `align_id_sequences` 为什么值得抽出来

这段 SQL 原先在两个套件里各写了一遍，写法是
`setval(seq, max(id) + 1, false)` —— 看上去没问题，其实是**双向**的：
它把游标设到"当前最大 id 之后"，**不管游标原本已经走到哪里**。

只要库里发生过"插了又删"（回归套件天天这么干），`max(id)` 就会**小于**序列
已经走过的位置。这时 `setval(max(id) + 1)` 是把游标**往回拨**，于是接下来新建的
行会去复用一批"历史上用过、后来删掉"的 id。id 复用本身不致命，但它会让**没有外键
的历史留痕**（如 `customer_merge_logs`）与新数据撞号——查询按 id 去捞留痕，捞到的
是上一轮留下的记录，业务逻辑据此得出一堆莫名其妙的结论（实测症状：回收站套件的
"最终有效客户"解析成 `None`，约 2~4 次全量回归红 1 次，且单独跑永不复现）。

正确写法是**只向前推**：取 `greatest(max(id) + 1, 序列当前值 + 1)`。
这样无论序列是被谁、以什么顺序动过，新 id 永远高于历史峰值，不可能撞上旧记录。
"""

import os
from urllib.parse import urlparse

from sqlalchemy import text

__all__ = ["align_id_sequences", "require_api_base", "require_isolated_db"]

#: 开发后端的端口：测试默认不许打
DEV_PORTS = {8000}
#: 正式库的库名：测试默认不许连
DEV_DATABASES = {"crm_sales_agent"}
#: 一次性库的命名规则（前缀或后缀命中其一即可）
ISOLATED_DB_PREFIXES = ("crm_iso", "crm_check", "crm_test")
ISOLATED_DB_SUFFIXES = ("_test",)
#: 逃生口：显式声明"我知道那是开发环境，我非要打"
ALLOW_ENV = "ALLOW_DEV_TARGETS"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _escape_hatch_on() -> bool:
    return (os.environ.get(ALLOW_ENV) or "").strip() == "1"


def _warn_escape_hatch(what: str) -> None:
    """放行了也得让人看见 —— 静默放行等于没有这道闸。"""
    print("!" * 72)
    print(f"!! 明知故犯模式（{ALLOW_ENV}=1）：{what}")
    print("!! 跑测试会真的写进它，后果自己承担。")
    print("!" * 72)


def require_api_base() -> str:
    """取接口地址，守住"不许打到开发后端"。返回去掉尾部斜杠的地址。

    没给就退出（**不给默认值**）：默认值本身才是问题 —— 它会让人以为
    "什么都没配也能跑"，而实际上跑的是开发后端。
    """
    base = (os.environ.get("API_BASE") or "").strip().rstrip("/")
    if not base:
        raise SystemExit(
            "必须显式设置 API_BASE（测试会真的建/改数据，不能默认打到开发后端 8000）。\n"
            "  例：API_BASE=http://127.0.0.1:8001/api/v1 "
            "DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_iso_test \\\n"
            "        PYTHONPATH=. .venv/bin/python scripts/<套件>.py"
        )
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK:
        raise SystemExit(f"API_BASE 不是本机地址，拒绝跑：{base}")
    if parsed.port in DEV_PORTS:
        if not _escape_hatch_on():
            raise SystemExit(
                f"API_BASE 指向 {parsed.port}（开发后端），拒绝跑：{base}\n"
                f"  测试会真的写库。真要临时验一次，设 {ALLOW_ENV}=1。"
            )
        _warn_escape_hatch(f"API_BASE 指的是开发后端 {base}")
    return base


def require_isolated_db() -> str:
    """守住"不许连正式库"，返回库名。

    必须在 `import app.*` **之前**调用：settings 在 import 时就定下了连哪个库，
    那之后再拦已经晚了（引擎已经指过去了）。
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库：库名以 crm_iso / crm_check / "
            "crm_test 开头，或以 _test 结尾）。\n"
            "  不设的话会悄悄读到 backend/.env 里的开发库 crm_sales_agent。"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if name in DEV_DATABASES:
        if _escape_hatch_on():
            _warn_escape_hatch(f"DATABASE_URL 指的是开发库 {name}")
            return name
        raise SystemExit(
            f"拒绝执行：DATABASE_URL 指向开发库 {name}。\n"
            f"  要临时验一次，设 {ALLOW_ENV}=1。"
        )
    if not (name.startswith(ISOLATED_DB_PREFIXES) or name.endswith(ISOLATED_DB_SUFFIXES)):
        raise SystemExit(
            f"拒绝执行：库名 {name!r} 不是一次性隔离库"
            f"（须以 {'/'.join(ISOLATED_DB_PREFIXES)} 开头，或以 _test 结尾）"
        )
    return name


async def align_id_sequences(session, tables) -> None:
    """把 `tables` 各自的 id 自增序列**只向前**推到现有数据之后。

    `session` 由调用方提供（本函数不 commit：跟着调用方的事务一起提交）。
    表名由调用方以**字面量**给出（不接收外部输入），因此可以安全地拼进 SQL。

    对每一张表做的是：

        setval(seq,
               greatest(coalesce(max(id), 0) + 1,
                        coalesce(pg_sequence_last_value(seq), 0) + 1),
               false)

    - `coalesce(max(id), 0) + 1`：序列至少要高于现有数据，否则紧接着的
      自增插入会撞主键（这是原写法**想**解决的问题）；
    - `pg_sequence_last_value(seq) + 1`：序列原本走到哪儿就至少停在哪儿，
      不再回拨（这是原写法**没**考虑到的另一半）；
    - 取两者的较大值，两种毛病一起解决。

    表没有 id 序列时（`pg_get_serial_sequence` 返回 NULL）直接跳过，不报错。
    """
    for table in tables:
        sequence = (
            await session.execute(
                text("select pg_get_serial_sequence(:t, 'id')"), {"t": table}
            )
        ).scalar_one_or_none()
        if sequence is None:
            continue
        await session.execute(
            text(
                "select setval("
                "  pg_get_serial_sequence(:t, 'id'),"
                "  greatest("
                f"    coalesce((select max(id) from {table}), 0) + 1,"
                "    coalesce(pg_sequence_last_value("
                "      pg_get_serial_sequence(:t, 'id')::regclass), 0) + 1"
                "  ),"
                "  false"
                ")"
            ),
            {"t": table},
        )
