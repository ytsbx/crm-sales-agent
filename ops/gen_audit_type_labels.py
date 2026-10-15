"""把前端的「审计对象类型中文名」生成到后端，供**中文搜索**用。

## 为什么需要这个脚本

审计日志页要支持"搜中文"（主人 2026-10-15 提的：「现在太乱了，也没有搜索功能」）。
但后端原来只能搜 `business_type` / `action` 两个字段，而它们是**内部英文码**
（`custom_inquiry`、`update_status`）—— 用户在搜索框里打「定制需求」永远搜不到，
因为中文名只存在于前端（显示时才翻译）。

要按中文搜，后端就得认识这些中文名。中文名的**真源在前端**
（`frontend/src/shared/audit-labels.ts` 的 `AUDIT_TYPE_LABEL`，66 条）——
那是给人看的表，也是页面实际显示的字。

**所以这里不手抄第二份**，而是从前端那份 TS 里解析出来、生成后端 JSON：
手抄两份的结果必然是"改了一处、忘了另一处"，而这类漂移在界面上表现为
"搜得到旧词、搜不到新词"，很难查。同一套做法项目里已有先例
（`audit_action_labels.json` 是真源、前端 TS 由它生成；这里是反方向）。

## 用法

    python3 ops/gen_audit_type_labels.py          # 生成
    python3 ops/gen_audit_type_labels.py --check  # 只核对是否最新（CI 用）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_TS = ROOT / "frontend" / "src" / "shared" / "audit-labels.ts"
BACKEND_JSON = ROOT / "backend" / "app" / "core" / "audit_type_labels.json"

#: 前端那张表的变量名
VAR_NAME = "AUDIT_TYPE_LABEL"


def parse_frontend_labels() -> dict[str, str]:
    """把前端 TS 里的 `AUDIT_TYPE_LABEL` 解析成 `{码: 中文名}`。"""
    source = FRONTEND_TS.read_text(encoding="utf-8")
    match = re.search(
        rf"export const {VAR_NAME}: Record<string, string> = \{{(.*?)\n\}}",
        source,
        re.S,
    )
    if not match:
        raise SystemExit(f"没在前端找到 {VAR_NAME}（文件或写法变了？）：{FRONTEND_TS}")
    pairs = re.findall(r"^\s*'?([A-Za-z_][A-Za-z0-9_]*)'?:\s*'([^']*)',", match.group(1), re.M)
    if not pairs:
        raise SystemExit(f"{VAR_NAME} 里一条都没解析出来（格式变了？）")
    return dict(pairs)


def render(labels: dict[str, str]) -> str:
    return json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    check_only = "--check" in sys.argv
    labels = parse_frontend_labels()
    content = render(labels)

    if check_only:
        if not BACKEND_JSON.exists():
            print(f"✗ 后端缺 {BACKEND_JSON.name}，先跑一次生成")
            return 1
        if BACKEND_JSON.read_text(encoding="utf-8") != content:
            print(f"✗ {BACKEND_JSON.name} 不是最新的（前端改了中文名？跑一次生成脚本）")
            return 1
        print(f"✓ 审计对象类型中文名是最新的（{len(labels)} 条）")
        return 0

    BACKEND_JSON.write_text(content, encoding="utf-8")
    print(f"✓ 已生成 {BACKEND_JSON.relative_to(ROOT)}（{len(labels)} 条，来源：前端 {VAR_NAME}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
