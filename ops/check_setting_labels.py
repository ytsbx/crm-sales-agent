#!/usr/bin/env python3
"""检查系统配置默认项都能在前端显示为中文名和中文说明。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend" / "app" / "modules" / "settings" / "service.py"
FRONTEND = REPO / "frontend" / "src" / "shared" / "setting-labels.ts"


def backend_keys() -> set[str]:
    text = BACKEND.read_text(encoding="utf-8")
    block = text.split("DEFAULT_SETTINGS:", 1)[1].split("SETTING_NUMBER_RULES:", 1)[0]
    return set(re.findall(r'^\s{4}"([a-z_]+)"\s*:', block, flags=re.MULTILINE))


def frontend_keys() -> set[str]:
    text = FRONTEND.read_text(encoding="utf-8")
    block = text.split("SETTING_LABELS", 1)[1].split("}\n\nexport function", 1)[0]
    return set(re.findall(r'^\s{2}([a-z_]+):\s*\{', block, flags=re.MULTILINE))


def main() -> int:
    back = backend_keys()
    front = frontend_keys()
    missing = sorted(back - front)
    extra = sorted(front - back)
    print(f"后端默认配置 {len(back)} 项，前端中文配置 {len(front)} 项")
    if missing:
        print("✗ 后端新增配置没有中文名/说明：" + "、".join(missing))
    if extra:
        print("✗ 前端存在后端没有的配置名：" + "、".join(extra))
    if missing or extra:
        return 1
    print("✓ 系统配置全部有中文名和中文说明")
    return 0


if __name__ == "__main__":
    sys.exit(main())
