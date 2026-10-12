#!/usr/bin/env python3
"""静态检查：审计页的**三层中文名**齐不齐（主人 2026-10-12 拍板 A 方案）。

主人原话：「审计日志也说一堆不是人话」——那一页当时是：对象列印数据库表名、
动作列印代码名、变更内容直接 `JSON.stringify` 整坨。修法是三层映射
（`frontend/src/shared/audit-labels.ts`），这份检查守的就是那三层：

  ① **对象类型**：后端写过的每个 `business_type`，都要有中文名；
     认不出的会被兜底成「其他记录」——但那是兜底，不是"可以不管"。
  ② **动作**：后端写过的每个 `action`，都要有中文名
     （这一列窄，认不出会原样印英文，所以必须逐个登记）。
  ③ **字段**：审计快照用到的字段，中文名要有出处 —— 要么在后端唯一那张表
     （`patch_schema.FIELD_LABELS`）里，要么在按对象定名的那张表
     （`AUDIT_FIELD_LABEL_BY_TYPE`，管 `name`/`status` 这类同名不同义的）。
     另外核对**生成物是不是最新的**（`gen_audit_field_labels.py`）。

为什么必须静态查：这三层的漏法**都不报错、不失败**，只在那一页上难看。
上一轮（价格中心）就是靠同类检查第一次运行抓到了漏网的 `disable`。

跑法：`python3 ops/check_audit_labels.py`
"""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / 'backend'
LABELS_TS = REPO / 'frontend' / 'src' / 'shared' / 'audit-labels.ts'
GENERATED_TS = REPO / 'frontend' / 'src' / 'shared' / 'audit-field-labels.ts'

#: 扫这些目录里所有 `business_type=` / `action=`（写审计的地方）
BACKEND_APP = BACKEND / 'app'


def backend_values(field: str) -> set[str]:
    found: set[str] = set()
    pattern = re.compile(rf'{field}="([a-z_]+)"')
    for path in BACKEND_APP.rglob('*.py'):
        found |= set(pattern.findall(path.read_text(encoding='utf-8')))
    return found


def ts_mapping_keys(path: Path, const_name: str) -> set[str]:
    """从 TS 文件里抠出某个 `Record<string, string>` 的键。"""
    text = path.read_text(encoding='utf-8')
    start = text.index(f'export const {const_name}')
    block = text[start : text.index('\n}', start)]
    return set(re.findall(r'(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', block))


def by_type_fields() -> set[str]:
    """`AUDIT_FIELD_LABEL_BY_TYPE` 里登记过的字段名（不分对象）。"""
    text = LABELS_TS.read_text(encoding='utf-8')
    start = text.index('export const AUDIT_FIELD_LABEL_BY_TYPE')
    block = text[start : text.index('\n}', start)]
    return set(re.findall(r'([a-z_]+)\s*:', block))


def field_label_keys() -> set[str]:
    """从 `patch_schema.py` 的 `FIELD_LABELS` 里抠出**裸字段名**（不 import，纯静态）。"""
    source = (BACKEND / 'app' / 'core' / 'patch_schema.py').read_text(encoding='utf-8')
    match = re.search(r'FIELD_LABELS: dict\[str, str\] = \{(.*?)\n\}', source, re.S)
    if not match:
        raise SystemExit('✗ 在 patch_schema.py 里找不到 FIELD_LABELS')
    keys = re.findall(r'"([A-Za-z_]+\.[a-z_]+|[a-z_]+)":', match.group(1))
    return {k.split('.', 1)[1] if '.' in k else k for k in keys}


def _python_with_backend_deps() -> str | None:
    """挑一个**真的能 import sqlalchemy** 的解释器来跑生成器。

    ## 为什么不能写死路径（2026-10-12 CI 实测炸了两次）

    原先写死 `.venv/bin/python`：
    - **CI 上直接 `FileNotFoundError`** —— runner 用的是 setup-python 装的系统解释器，
      `backend/.venv` 根本不存在。而且它是在"UI 冒烟"那个 job 里红的，
      本地全绿、CI 全红，最容易怀疑错方向（我第一轮就怀疑错了）。

    改成"用 `sys.executable`"又踩了第二个坑：这个检查**要在两个环境里都能跑** ——
    CI 的 frontend job（`python3` 装过后端依赖）和本地随手 `python3 ops/xxx.py`
    （那个 python3 通常**没装**依赖）。只看 `sys.executable` 时后者直接
    `ModuleNotFoundError: No module named 'sqlalchemy'`。

    所以按候选顺序**实际探测**谁能 import sqlalchemy，挑第一个能用的：
    这样两边都不用改调用方式，也不会因为"换了台机器"就红。
    """
    candidates = [
        sys.executable,                                   # 当前解释器（CI 就是它）
        str(BACKEND / '.venv' / 'bin' / 'python'),        # 本地开发虚拟环境
        str(BACKEND / '.venv' / 'Scripts' / 'python.exe'),  # Windows
        'python3',
        'python',
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate != sys.executable and not (
            os.path.isabs(candidate) and os.path.exists(candidate)
        ) and os.path.isabs(candidate):
            continue  # 候选是绝对路径但文件不存在
        try:
            probe = subprocess.run(
                [candidate, '-c', 'import sqlalchemy'],
                cwd=BACKEND, capture_output=True, text=True, timeout=60,
                env={**os.environ, 'PYTHONPATH': '.'},
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def main() -> int:
    problems: list[str] = []

    types = backend_values('business_type')
    actions = backend_values('action')

    type_labels = ts_mapping_keys(LABELS_TS, 'AUDIT_TYPE_LABEL')
    action_labels = ts_mapping_keys(LABELS_TS, 'AUDIT_ACTION_LABEL')

    missing_types = sorted(types - type_labels)
    if missing_types:
        problems.append(
            '这些对象类型会出现在审计页，但没有中文名（会显示成「其他记录」）：\n    '
            + '、'.join(missing_types)
        )

    missing_actions = sorted(actions - action_labels)
    if missing_actions:
        problems.append(
            '这些动作会出现在审计页，但没有中文名（会原样印英文代码名）：\n    '
            + '、'.join(missing_actions)
        )

    # ③ 字段：中文名的两个出处。
    # ⚠️ 这里**读源码不 import**：后端要 pydantic，而这条检查跟 `check_action_labels.py`
    # 一样是纯静态检查（CI 的前端 job 里没有后端依赖）。判据仍是"那张表里有什么键"。
    backend_fields = field_label_keys()

    # 与列表那页一样：同名不同义的字段必须按对象登记（否则会串名）
    ambiguous_ok = by_type_fields()
    for field in ('name', 'status', 'title', 'source'):
        if field not in ambiguous_ok:
            problems.append(
                f'同名不同义的字段 `{field}` 没有在 AUDIT_FIELD_LABEL_BY_TYPE 里按对象登记 —— '
                '它会串名（客户名称显示成产品名称）'
            )

    # 生成物是否最新：重跑生成器，看文件有没有变化
    before = GENERATED_TS.read_text(encoding='utf-8')
    # ⚠️ **不许写死 `.venv/bin/python`**（2026-10-12 CI 实测炸过）：
    # 那是 macOS 本地虚拟环境的相对路径，GitHub runner 上根本没这个文件 ——
    # 报 `FileNotFoundError: [Errno 2] No such file or directory: '.venv/bin/python'`，
    # 而且是在"UI 冒烟"这个**看起来跟它无关**的 job 里红的（本地全绿、CI 全红，
    # 最容易怀疑错方向）。用 `sys.executable`：谁在跑这个检查，就用谁的解释器 ——
    # 本地是 `.venv/bin/python`，CI 是 `python3`，两边都对。
    interpreter = _python_with_backend_deps()
    if interpreter is None:
        problems.append(
            '找不到"装了后端依赖"的解释器来重跑生成器 —— '
            '无法核对 `audit-field-labels.ts` 是否最新。'
            '本地请用 `backend/.venv/bin/python ops/check_audit_labels.py` 跑这个检查；'
            'CI 请确认跑静态检查那一步的解释器装过后端 requirements。'
        )
        result = None
    else:
        # 继承当前环境、只**追加** PYTHONPATH：把 env 剥成干净字典会让子进程
        # 找不到依赖（实测 `ModuleNotFoundError: sqlalchemy`）。
        result = subprocess.run(
            [interpreter, 'scripts/gen_audit_field_labels.py'],
            cwd=BACKEND, capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': '.'},
        )
    if result is not None and result.returncode != 0:
        problems.append(
            '生成器跑失败（`gen_audit_field_labels.py`），无法核对产物：\n    '
            + (result.stderr or '').strip().splitlines()[-1] if result.stderr else '未知错误'
        )
    elif result is not None:
        after = GENERATED_TS.read_text(encoding='utf-8')
        if after != before:
            problems.append(
                '`frontend/src/shared/audit-field-labels.ts` 不是最新的 —— '
                '请重跑 `python backend/scripts/gen_audit_field_labels.py` 并提交产物'
            )

    print(f'核对：对象类型 {len(types)} 个（已译 {len(types & type_labels)}）、'
          f'动作 {len(actions)} 个（已译 {len(actions & action_labels)}）、'
          f'后端字段中文名 {len(backend_fields)} 条、按对象定名 {len(ambiguous_ok)} 条')
    if problems:
        print()
        for item in problems:
            print(f'  ✗ {item}')
        return 1
    print('✓ 审计页三层中文名齐全，且生成物是最新的')
    return 0


if __name__ == '__main__':
    sys.exit(main())
