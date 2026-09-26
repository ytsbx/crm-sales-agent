"""静态检查：代码里引用的权限码是否真的存在于权限目录。

背景：`require_permission("lead:manage")` 这种写法，如果权限码在
`permissions` 表里不存在，接口就**永远只对 admin 可用** —— 是个安静的
死代码（不会报错、不会有日志、授权界面里也找不到这一项）。
本会话已经踩过一次（lead:manage）。

做法：AST 扫出所有 `require_permission("...")` 的字符串字面量，
与 seed 里定义的权限目录比对。
"""

import ast
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def catalog_codes() -> set[str]:
    """从 seed.py 的 PERMISSIONS 列表里取出全部权限码。"""
    seed = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'seed.py'
    text = seed.read_text(encoding='utf-8')
    match = re.search(
        r'^PERMISSIONS(?::[^=]+)?\s*=\s*\[(.*?)^\]', text, re.S | re.M
    )
    if not match:
        raise SystemExit('未能在 seed.py 里定位 PERMISSIONS 列表')
    return set(re.findall(r'\("([^"]+)"', match.group(1)))


def used_codes() -> dict[str, list[str]]:
    """扫出代码里 require_permission 用到的权限码 -> 出现位置。"""
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
            if name != 'require_permission':
                continue
            rel = path.relative_to(root.parent).as_posix()
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, []).append(f'{rel}:{node.lineno}')
    return found


if __name__ == '__main__':
    catalog = catalog_codes()
    used = used_codes()
    print(f'权限目录 {len(catalog)} 个；代码里用到 {len(used)} 个')

    unknown = {code: locs for code, locs in used.items() if code not in catalog}
    unused = sorted(catalog - set(used))

    if unknown:
        print(f'\n发现 {len(unknown)} 个不存在的权限码（接口会永远只对 admin 可用）：')
        for code in sorted(unknown):
            print(f'  {code}  <- {", ".join(unknown[code])}')
    if unused:
        print(f'\n目录里定义但代码没用到（{len(unused)} 个，可能是冗余或有待接线）：')
        print('  ' + '、'.join(unused))

    if unknown:
        sys.exit(1)
    print('\n代码引用的权限码全部存在于目录中')
