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


#: 有意**不写进接口文档**的接口：纯辅助类，平时没人照它对接。
#: 反向对账（--reverse）用这份白名单过滤噪音；改这份名单等于改口径，
#: 加东西前先想清楚"这是不是真的不该写进设计基线"。
DOC_EXEMPT_PREFIXES = (
    # 导入模板下载 / 导入执行（前端按钮直接用，不对外）
    '/customers/export', '/customers/import-template',
    '/leads/export', '/leads/import-template',
    '/products/export', '/products/import-template',
    '/skus/export', '/skus/import-template',
    '/costs/import-template', '/costs/import',
    '/price-rules/import-template', '/price-rules/import',
    '/customer-price-rules/import-template', '/customer-price-rules/import',
    # 前端埋点、基础设施
    '/usage/timings', '/meta/config', '/search', '/agent/tools',
    '/files/{}/preview',
    # 外部系统回调（由对方调用，不是本系统对外提供）
    '/webhooks/wecom/events',
)


def reverse_missing() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """代码注册了、文档没写的接口。

    返回（业务类缺口, 有意跳过的辅助类）。2026-10-06 加：
    原来只做单向对账（文档有代码没有），永远报 0 条，
    结果是**文档悄悄落后了 113 条也没人发现**（案例库、新品洞察、合同、
    对外单据、订单草稿、销售目标等整章都没写）。反向必须一起查。
    """
    actual = registered_routes()
    documented = {key for key in documented_endpoints()}
    missing = {key for key in actual if key not in documented}
    skipped = {
        key for key in missing
        if any(key[1].startswith(prefix) for prefix in DOC_EXEMPT_PREFIXES)
    }
    return missing - skipped, skipped


def main() -> int:
    argv = sys.argv[1:]
    # 默认行为与加这个选项之前**完全一致**（有人可能拿它的输出做别的事）
    do_forward = ('--reverse' not in argv) or ('--both' in argv)
    do_reverse = ('--reverse' in argv) or ('--both' in argv)
    # --strict：反向也不许有业务类缺口，用于挂进回归（非零退出＝有缺口）
    strict = '--strict' in argv

    rc = 0
    if do_forward:
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
        if missing:
            rc = 1

    if do_reverse:
        real, skipped = reverse_missing()
        print()
        print(f'代码有、文档没有（业务类）：{len(real)} 条')
        for method, path in sorted(real):
            print(f'    {method:6} {path}')
        print(f'代码有、文档没有（有意跳过的辅助类）：{len(skipped)} 条'
              f'（清单见 DOC_EXEMPT_PREFIXES）')
        if real:
            print()
            print('业务类缺口应补进 03-API —— 文档落后于代码时，'
                  '对账就只剩单向可用，下一批改动又会漏同样的事。')
            if strict:
                rc = 1

    return rc


if __name__ == '__main__':
    raise SystemExit(main())

