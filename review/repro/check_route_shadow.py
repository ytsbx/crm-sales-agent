"""静态检查：是否有静态路由被前面注册的动态路由抢占（B24 控制项）。

运行：cd backend && PYTHONPATH=. .venv/bin/python ../review/repro/check_route_shadow.py
"""
from fastapi.routing import APIRoute, _IncludedRouter
from starlette.routing import Route

from app.main import app

entries = []


def walk(routes, prefix=""):
    for route in routes:
        if isinstance(route, _IncludedRouter):
            walk(route.original_router.routes, prefix + (route.include_context.prefix or ""))
        elif isinstance(route, APIRoute):
            entries.append((prefix + route.path, set(route.methods or ())))
        elif isinstance(route, Route):
            entries.append((prefix + getattr(route, "path", ""), set(getattr(route, "methods", ()) or ())))


walk(app.routes)


def segments(path):
    return [part for part in path.split("/") if part]


def dynamic(part):
    return part.startswith("{") and part.endswith("}")


shadows = []
for index, (left_path, left_methods) in enumerate(entries):
    left = segments(left_path)
    for right_path, right_methods in entries[index + 1:]:
        right = segments(right_path)
        if len(left) != len(right) or not (left_methods & right_methods):
            continue
        matches = all(dynamic(a) or a == b for a, b in zip(left, right))
        has_dynamic = any(dynamic(a) and not dynamic(b) for a, b in zip(left, right))
        if matches and has_dynamic:
            shadows.append((left_path, right_path, sorted(left_methods & right_methods)))

print(f"routes={len(entries)} shadows={len(shadows)}")
for item in shadows:
    print("SHADOW", item)
raise SystemExit(1 if shadows else 0)
