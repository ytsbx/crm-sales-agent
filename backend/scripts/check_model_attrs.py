"""静态检查：ORM 模型上被访问但不存在的列名。

背景：本会话已经连续三次写出"模型上不存在的列"（`Quote.remark`、
`Quote.approval_status`、`SalesOrder.contact_id`），每次都只在运行时
才炸成 500。这类错误完全可以静态查出来。

做法：扫 app/modules/**/*.py，抓出 `SomeModel.attr` 形式（首字母大写、
且 SomeModel 是已知 ORM 模型名）的属性访问，逐个比对模型列集合
（ORM 映射列 + 混入列 + 关系）。

范围故意收窄到"模型名.属性"，避免误报；宁可漏报也不刷屏。
"""

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def model_columns() -> dict[str, set[str]]:
    """收集所有 ORM 模型的可用属性名。"""
    # 与 alembic/env.py 同样先 import 一次，保证所有模型都注册进 mapper
    import app.core.audit  # noqa: F401
    import app.modules.agent.model  # noqa: F401
    import app.modules.approval.model  # noqa: F401
    import app.modules.customer.model  # noqa: F401
    import app.modules.file.model  # noqa: F401
    import app.modules.followup.model  # noqa: F401
    import app.modules.integration.model  # noqa: F401
    import app.modules.lead.model  # noqa: F401
    import app.modules.notification.model  # noqa: F401
    import app.modules.opportunity.model  # noqa: F401
    import app.modules.order.model  # noqa: F401
    import app.modules.payment.model  # noqa: F401
    import app.modules.pricing.model  # noqa: F401
    import app.modules.product.model  # noqa: F401
    import app.modules.quote.model  # noqa: F401
    import app.modules.sample.model  # noqa: F401
    import app.modules.settings.model  # noqa: F401
    import app.modules.task.model  # noqa: F401
    import app.modules.user.model  # noqa: F401
    import app.modules.wecom.model  # noqa: F401
    from app.core.base import Base

    result: dict[str, set[str]] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        names = set(mapper.columns.keys())
        names |= {rel.key for rel in mapper.relationships}
        names |= set(vars(cls).keys())
        result[cls.__name__] = names
    return result


def scan(models: dict[str, set[str]]) -> list[str]:
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    problems: list[str] = []
    for path in sorted(root.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if not isinstance(value, ast.Name):
                continue
            name = value.id
            if name not in models:
                continue
            if node.attr not in models[name]:
                rel = path.relative_to(root.parent).as_posix()
                problems.append(f'{rel}:{node.lineno}: {name}.{node.attr}')
    return problems


if __name__ == '__main__':
    cols = model_columns()
    print(f'已加载 {len(cols)} 个 ORM 模型')
    bad = scan(cols)
    if bad:
        print(f'\n发现 {len(bad)} 处不存在的列/关系：')
        for item in bad:
            print('  ' + item)
        sys.exit(1)
    print('未发现访问不存在的列')
