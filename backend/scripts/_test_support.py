"""套件之间共用的测试辅助（不是业务代码，不进任何接口路径）。

只放**多个套件真正重复、且写错一次就会全体踩坑**的东西。
目前只有一件：把自增序列对齐到现有数据之后。

## 为什么值得抽出来

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

from sqlalchemy import text

__all__ = ["align_id_sequences"]


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
