"""把 03-API 文档的接口与实际注册路由对账，并按模块归类缺口。

上一轮的对账脚本删掉了（避免被误跑），这里重写一版留档用。
关键点（我踩过）：新版 FastAPI 的 include_router 产生 `_IncludedRouter`，
真实路由在 `original_router.routes`，且其中 path 不含挂载前缀，
前缀在 `include_context.prefix`。
"""

import re
import sys

sys.path.insert(0, '.')

from app.main import app  # noqa: E402

DOC = '../03-API-V1.1-接口设计文档.md'
PREFIX = '/api/v1'

actual: set[tuple[str, str]] = set()
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
            actual.add((method, normalized))

doc_entries: list[tuple[str, str, int]] = []
with open(DOC, encoding='utf-8') as handle:
    for number, line in enumerate(handle, start=1):
        match = re.match(r'^-\s+`(GET|POST|PUT|PATCH|DELETE)\s+([^`]+)`', line.strip())
        if match:
            path = re.sub(r'\{[^}]+\}', '{}', match.group(2).strip().split('?')[0])
            doc_entries.append((match.group(1), path, number))

seen: set[tuple[str, str]] = set()
missing: list[tuple[str, str]] = []
for method, path, _ in doc_entries:
    key = (method, path)
    if key in seen:
        continue
    seen.add(key)
    if key not in actual:
        missing.append(key)

print(f'文档接口去重后 {len(seen)} 条；实际注册路由 {len(actual)} 条')
print(f'文档有、代码没有：{len(missing)} 条')
print()

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
    '其他': [],
}

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
