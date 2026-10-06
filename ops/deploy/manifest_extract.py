#!/usr/bin/env python3
"""读取备份清单 manifest.json，按需导出其中的 TSV 片段。

为什么要单独一个脚本：恢复演练脚本（bash）需要在两个地方消费清单里的结构化字段
（关键台账行数、历史单据引用）。在 bash 里嵌 heredoc 调 python 容易被引号/缩进
坑到，而且清单格式一旦演进就得改多处解析。这里集中成一个只读工具：

    python3 ops/deploy/manifest_extract.py --manifest <路径> --field table_counts
    python3 ops/deploy/manifest_extract.py --manifest <路径> --field doc_references

输出为制表符分隔的行（与备份目录里同名 .tsv 完全一致的列序），行数不足的字段补空串。
退出码：0 正常；2 参数/文件错误；3 清单不是合法 JSON（此时调用方应判定"清单不可信"）。
"""

from __future__ import annotations

import argparse
import json
import sys

#: 允许导出的字段及各自列数：列数与 backup_db.sh 写清单时的 json_tsv_array 一一对应。
FIELDS = {
    "table_counts": 2,
    "templates": 4,
    "doc_references": 5,
    "render_env": 3,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="从备份清单里导出 TSV 片段")
    parser.add_argument("--manifest", required=True, help="备份目录里的 manifest.json")
    parser.add_argument("--field", required=True, choices=sorted(FIELDS), help="要导出的字段")
    args = parser.parse_args()

    try:
        with open(args.manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError:
        print(f"ERROR 清单不存在：{args.manifest}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        # 清单损坏必须让调用方明确知道：按损坏清单核对会得出"一切正常"的假结论。
        print(f"ERROR 清单不是合法 JSON（{args.manifest}）：{exc}", file=sys.stderr)
        return 3

    rows = manifest.get(args.field)
    if rows is None:
        print(f"ERROR 清单里没有字段 {args.field}：这份清单版本不支持该核对项", file=sys.stderr)
        return 2

    width = FIELDS[args.field]
    for row in rows:
        cells = [str(cell) for cell in row][:width]
        cells += [""] * (width - len(cells))
        print("\t".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
