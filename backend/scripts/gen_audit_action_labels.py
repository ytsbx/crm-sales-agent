#!/usr/bin/env python3
"""从共用真源生成前端动作名表。

用法：
    cd backend && PYTHONPATH=. .venv/bin/python scripts/gen_audit_action_labels.py

## 为什么要生成

动作中文名只有一个真源：`backend/app/core/audit_action_labels.json`
（后端时间线 `timeline/service.py` 直接读它拼标题）。

前端也要用同一份（审计页），但**不能跨包 import**：前端根目录是 `frontend/`，
Vite 与 `tsconfig` 都不允许 import 到它外面。所以用**生成**——
与 `audit-field-labels.ts` 同一手法，`ops/check_audit_labels.py` 会核对
"生成物是不是最新的"，不会悄悄漂移。

（为什么不把 JSON 放前端、让后端去读：后端读前端文件更别扭，
而且前端目录将来可能整块替换。数据在后端、前端用生成物，方向更顺。）
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "backend" / "app" / "core" / "audit_action_labels.json"
TARGET = REPO / "frontend" / "src" / "shared" / "audit-action-labels.generated.ts"

HEADER = """/**
 * 动作中文名 —— **生成物，不要手改**。
 *
 * 真源：`backend/app/core/audit_action_labels.json`
 * 生成：`cd backend && PYTHONPATH=. .venv/bin/python scripts/gen_audit_action_labels.py`
 * 核对：`cd frontend && python3 ../ops/check_audit_labels.py`
 *
 * 这个文件是为了绕开"前端不能 import 到包外"而生成的（同一手法见
 * `audit-field-labels.ts`）。两处若不一致，检查会红。
 */
export const SHARED_ACTION_LABELS: Record<string, string> = {
"""


def main() -> int:
    labels: dict[str, str] = json.loads(SOURCE.read_text(encoding="utf-8"))
    lines = [HEADER]
    for key in sorted(labels):
        value = labels[key].replace("\\", "\\\\").replace("'", "\\'")
        # ⚠️ 键**必须引号包起来**：动作码里有带命名空间冒号的（`oa:resolve`、
        # `customer:create`），不加引号就不是合法 TS（`oa:resolve: '…'`）——
        # 我第一版没加，tsc 当场报 `',' expected`。
        lines.append(f"  '{key}': '{value}',\n")
    lines.append("}\n")
    TARGET.write_text("".join(lines), encoding="utf-8")
    print(f"✓ 写入 {TARGET.relative_to(REPO)}：{len(labels)} 个动作中文名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
