#!/usr/bin/env python
"""把 03-API 文档里的接口与实际注册路由逐条对账。

用法（在 backend/ 目录下）：
    .venv/Scripts/python.exe ../ops/api_gap.py

用途：文档里有 360+ 条接口，靠人逐条核一定会漏（第一版就是这么漏掉 124 条的）。
这个脚本给出"文档有、代码没有"的清单，并按模块归类，便于排批次补齐。

**注意**：报出来的是"路径对不上"，不等于"功能缺失" ——
有的功能实现成了别的路径（例如文档 `POST /quote-versions/{id}/generate-pdf`
实现为 `GET /quote-versions/{id}/pdf`），所以清单要人工再看一遍。

两个实现的坑（都踩过）：
1. 新版 FastAPI 的 include_router 会产生 `_IncludedRouter` 包装对象，
   `app.routes` 只给一层；真实路由在 `original_router.routes`；
2. 其中 path **不含**挂载前缀，前缀在 `include_context.prefix`。
   不处理这两点，一条都比对不上。
"""

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402

DOC = BACKEND.parent / '03-API-V1.1-接口设计文档.md'
PREFIX = '/api/v1'

GROUPS = {
    '认证/用户/角色/部门': ['/auth/', '/users', '/roles', '/permissions', '/departments'],
    '线索': ['/leads'],
    '客户/联系人/公海': ['/customers', '/contacts', '/public-pool'],
    '商机': ['/opportunities', '/opportunity-', '/loss-reasons'],
    '产品/价格/核价': ['/products', '/skus', '/costs', '/price-', '/pricing/', '/exchange-rates'],
    '报价': ['/quotes', '/quote-'],
    '审批': ['/approval'],
    '跟进/任务': ['/followups', '/tasks', '/task-rules'],
    '订单/回款': ['/orders', '/receivables', '/payments'],
    'Agent': ['/agent/'],
    '企微/ERP 集成': ['/integrations/', '/webhooks/'],
    '其他': [],
}


def registered_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        original = getattr(route, 'original_router', None)
        if original is None:
            continue
        context = getattr(route, 'include_context', None)
        mount = getattr(context, 'prefix', '') or ''
        for item in getattr(original, 'routes', []) or []:
            path = getattr(item, 'path', None)
            methods = getattr(item, 'methods', None)
            if not path or not methods:
                continue
            full = f'{mount}{path}'
            if not full.startswith(PREFIX):
                continue
            normalized = re.sub(r'\{[^}]+\}', '{}', full[len(PREFIX):] or '/')
            for method in methods:
                if method in ('HEAD', 'OPTIONS'):
                    continue
                found.add((method, normalized))
    return found


def documented_endpoints() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    with open(DOC, encoding='utf-8') as handle:
        for line in handle:
            match = re.match(
                r'^-\s+`(GET|POST|PUT|PATCH|DELETE)\s+([^`]+)`', line.strip()
            )
            if match:
                path = re.sub(r'\{[^}]+\}', '{}', match.group(2).strip().split('?')[0])
                entries.append((match.group(1), path))
    return entries


def main() -> int:
    actual = registered_routes()
    seen: set[tuple[str, str]] = set()
    missing: list[tuple[str, str]] = []
    for method, path in documented_endpoints():
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        if key not in actual:
            missing.append(key)

    print(f'文档接口去重后 {len(seen)} 条；实际注册路由 {len(actual)} 条')
    print(f'文档有、代码没有：{len(missing)} 条')
    print()

    buckets: dict[str, list[tuple[str, str]]] = {name: [] for name in GROUPS}
    for method, path in missing:
        for name, prefixes in GROUPS.items():
            if name == '其他':
                continue
            if any(path.startswith(prefix) for prefix in prefixes):
                buckets[name].append((method, path))
                break
        else:
            buckets['其他'].append((method, path))

    for name, rows in buckets.items():
        if not rows:
            continue
        print(f'【{name}】{len(rows)} 条')
        for method, path in rows:
            print(f'    {method:6} {path}')
        print()

    print('提示：以上是"路径对不上"，不等于"功能缺失"，需人工复核一遍。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
