#!/usr/bin/env python3
"""执行 shared_checks.json：项目共用，顺序分别登记，环境阶段由入口保留。

CI 默认遇错停止；本地 --keep-going 逐项执行并通过 --failures-file
把失败名称交回 run_checks.sh 的 FAILED 数组。仅使用 Python 标准库。
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).with_suffix('.json')


def load_manifest(path=MANIFEST):
    groups = json.loads(path.read_text(encoding='utf-8'))['groups']
    if not groups:
        raise ValueError('共用检查清单不能为空')
    names = set()
    for group_name, group in groups.items():
        checks = group['checks']
        if not checks or group['kind'] not in ('python', 'node'):
            raise ValueError(f'{group_name}: 检查组为空或执行类型无效')
        for profile in ('local', 'ci'):
            if not (ROOT / group['cwd'][profile]).is_dir():
                raise ValueError(f'{group_name}: {profile} 工作目录不存在')
            orders = [check[f'{profile}_order'] for check in checks]
            if any(type(order) is not int or order < 1 for order in orders):
                raise ValueError(f'{group_name}: {profile} 顺序必须是正整数')
            if len(set(orders)) != len(orders):
                raise ValueError(f'{group_name}: {profile} 顺序重复')
        for check in checks:
            if check['name'] in names:
                raise ValueError(f"重复检查项: {check['name']}")
            names.add(check['name'])
            if (not check['args'] or not all(isinstance(arg, str) and arg for arg in check['args'])):
                raise ValueError(f"{check['name']}: 缺少命令参数")
            for profile in ('local', 'ci'):
                # unittest的-p参数是匹配模式，不是相对工作目录的脚本。
                script_args = check['args'][:1] if group['kind'] == 'python' else check['args'][1:2]
                for arg in script_args:
                    if arg.endswith(('.py', '.mjs')):
                        target = ROOT / group['cwd'][profile] / arg.replace('{root}', str(ROOT))
                        if not target.is_file():
                            raise ValueError(f"{check['name']}: 文件不存在: {target}")
    # 本地原先以glob执行这类静态检查，改为清单后仍不能漏掉新文件。
    # Node只迁移两个入口已登记的并集，不自动扩大到独立手工测试。
    for group_name, pattern in (('frontend-static', 'check_*.py'),):
        actual = {p.name for p in (ROOT / 'ops').glob(pattern)}
        registered = {check['name'] for check in groups[group_name]['checks']}
        if actual != registered:
            raise ValueError(f'{group_name}: 未登记={sorted(actual - registered)}, 多余={sorted(registered - actual)}')
    return groups


def ordered_checks(groups, group_name, profile):
    return sorted(groups[group_name]['checks'], key=lambda check: check[f'{profile}_order'])


def run_group(groups, group_name, profile, *, keep_going=False):
    group = groups[group_name]
    cwd = ROOT / group['cwd'][profile]
    executable = sys.executable if group['kind'] == 'python' else 'node'
    failed = []
    for check in ordered_checks(groups, group_name, profile):
        name = check['name']
        command = [executable, *(arg.replace('{root}', str(ROOT)) for arg in check['args'])]
        print(f'== {name} ==', flush=True)
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=keep_going, text=keep_going)
            success = result.returncode == 0
            if not success and keep_going:
                print('\n'.join((result.stdout + result.stderr).splitlines()[-4:]))
        except OSError as exc:
            print(f'{name}: {exc}', file=sys.stderr)
            success = False
        print(f'{"OK" if success else "FAIL"}  {name}', flush=True)
        if not success:
            failed.append(name)
            if not keep_going:
                break
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group')
    parser.add_argument('--profile', choices=('local', 'ci'), required=True)
    parser.add_argument('--list', action='store_true', help='只列出顺序，不运行检查')
    parser.add_argument('--keep-going', action='store_true')
    parser.add_argument('--failures-file', type=Path)
    args = parser.parse_args()
    if args.failures_file:
        args.failures_file.write_text('', encoding='utf-8')
    try:
        groups = load_manifest()
        if args.group not in groups:
            raise ValueError(f'未知检查组: {args.group}')
        if args.list:
            for check in ordered_checks(groups, args.group, args.profile):
                print(check['name'])
            return 0
        failed = run_group(groups, args.group, args.profile, keep_going=args.keep_going)
        if args.failures_file:
            args.failures_file.write_text(''.join(name + '\n' for name in failed), encoding='utf-8')
        return int(bool(failed))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'共用检查清单错误: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
