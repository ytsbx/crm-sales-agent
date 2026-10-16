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


#: 名字本身不是"模型列"的常见成员（字典/集合方法、内置属性）。
#: 只用于**少报**：命中就放过，不报问题。
_COMMON_NON_COLUMNS = {
    'get', 'keys', 'values', 'items', 'pop', 'setdefault', 'update', 'copy',
    'append', 'extend', 'insert', 'remove', 'clear', 'index', 'count', 'sort',
    'metadata', 'registry', 'query', 'columns', 'c', 'table', '__dict__',
    # 这些是 （认证用的数据类，**不是 ORM 模型**）上的真实成员：
    # 参数注解写成 User 时会误报。宁可少报。
    'roles', 'permissions', 'sid', 'has', 'scalars', 'unique', 'first', 'all',
}


def scan(models: dict[str, set[str]]) -> list[str]:
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    problems: list[str] = []
    for path in sorted(root.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        # ---- 第一类：`模型类.属性`（原有）----
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

        # ---- 第二类：**实例**上的属性访问（2026-10-16 补）----
        #
        # 为什么加这一类：线上出过一次 500 ——
        # `AttributeError: 'SampleRequest' object has no attribute 'sample_maker'`。
        # 那次是"给 schema/序列化加了字段、**忘了给模型加列**"，
        # 而访问写成 `request.sample_maker`（**实例**）—— 第一类只认 `模型类.属性`，
        # 于是完全没拦住，只有线上点开列表才炸。
        #
        # 判据用**函数参数的类型注解**（`request: SampleRequest`）：这是静态可得的、
        # 不猜的类型信息。局部变量（`sample = await ...`）不追 —— 追了容易误报，
        # 而参数注解已经覆盖了本项目里绝大多数"传进来的模型对象"。
        annotated: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if arg.annotation is None:
                    continue
                ann = arg.annotation
                # 三种写法都认：`SampleRequest`、`SampleRequest | None`、`Optional[SampleRequest]`
                if isinstance(ann, ast.BinOp):        # X | None
                    candidates = [ann.left, ann.right]
                elif isinstance(ann, ast.Subscript):  # Optional[X]
                    inner = ann.slice
                    candidates = inner.elts if isinstance(inner, ast.Tuple) else [inner]
                else:
                    candidates = [ann]
                for candidate in candidates:
                    if isinstance(candidate, ast.Name) and candidate.id in models:
                        annotated[arg.arg] = candidate.id
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in annotated:
                continue
            model = annotated[value.id]
            if node.attr in models[model]:
                continue
            # 抗误报：不认识的属性名分两种 ——
            #   · **所有模型上都不存在** → 真问题（就是我要抓的那一类）；
            #   · 别的模型上有这个名字（比如 `item.get` 里的 `get` 是字典方法，
            #     而 `WeComTransferItem` 只是同名/类型注解说岔了）→ 放过，宁可漏报。
            if any(node.attr in names for names in models.values()):
                continue
            if node.attr in _COMMON_NON_COLUMNS:
                continue
            rel = path.relative_to(root.parent).as_posix()
            problems.append(f'{rel}:{node.lineno}: {value.id}.{node.attr}（注解为 {model}）')
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
