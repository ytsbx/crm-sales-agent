"""共用检查调度及成本待办源核对回归：不访问数据库或网络。"""

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import shared_checks as checks


class SharedChecksTests(unittest.TestCase):
    def setUp(self):
        self.groups = checks.load_manifest()

    def invalid_manifest(self, mutate, exception=ValueError):
        manifest = {'groups': copy.deepcopy(self.groups)}
        mutate(manifest['groups'])
        with tempfile.TemporaryDirectory(prefix='crm-check-plan-') as directory:
            path = Path(directory) / 'checks.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(exception):
                checks.load_manifest(path)

    def test_profiles_cover_same_checks_once(self):
        for group in self.groups:
            local = checks.ordered_checks(self.groups, group, 'local')
            ci = checks.ordered_checks(self.groups, group, 'ci')
            self.assertEqual({c['name'] for c in local}, {c['name'] for c in ci})
            self.assertEqual(len(local), len({c['name'] for c in local}))

    def test_missing_ci_order_cannot_silently_skip_new_check(self):
        self.invalid_manifest(lambda groups: groups['frontend-tests']['checks'][0].pop('ci_order'), KeyError)

    def test_duplicate_order_is_rejected(self):
        self.invalid_manifest(lambda groups: groups['frontend-tests']['checks'][1].update(local_order=1))

    def test_missing_script_is_rejected(self):
        self.invalid_manifest(lambda groups: groups['frontend-tests']['checks'][0].update(
            args=['--test', '{root}/ops/missing-check.test.mjs']))

    def test_unregistered_frontend_static_check_is_rejected(self):
        self.invalid_manifest(lambda groups: groups['frontend-static']['checks'].pop())

    def run_fake(self, profile, keep_going):
        output = io.StringIO()
        result = subprocess.CompletedProcess([], 1, stdout='fake failure', stderr='')
        with patch.object(checks.subprocess, 'run', return_value=result) as run, redirect_stdout(output):
            failed = checks.run_group(self.groups, 'frontend-tests', profile, keep_going=keep_going)
        return failed, run

    def test_ci_stops_at_first_failure(self):
        failed, run = self.run_fake('ci', False)
        self.assertEqual(failed, ['request_key.test.mjs'])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['cwd'], checks.ROOT / 'frontend')

    def test_local_continues_and_reports_each_failure_in_order(self):
        failed, run = self.run_fake('local', True)
        expected = [c['name'] for c in checks.ordered_checks(self.groups, 'frontend-tests', 'local')]
        self.assertEqual(failed, expected)
        self.assertEqual(run.call_count, len(expected))
        self.assertEqual(run.call_args.kwargs['cwd'], checks.ROOT / 'backend')
        self.assertTrue(all(call.args[0][0:2] == ['node', '--test'] for call in run.call_args_list))

    def test_missing_executable_is_a_failure_and_local_continues(self):
        with patch.object(checks.subprocess, 'run', side_effect=FileNotFoundError('missing node')) as run:
            with redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                failed = checks.run_group(self.groups, 'frontend-tests', 'local', keep_going=True)
        self.assertEqual(len(failed), len(self.groups['frontend-tests']['checks']))
        self.assertEqual(run.call_count, len(failed))

    def test_cli_returns_failure_names_for_shell_summary(self):
        with tempfile.TemporaryDirectory(prefix='crm-check-failures-') as directory:
            path = Path(directory) / 'failed.txt'
            argv = ['shared_checks.py', 'frontend-tests', '--profile', 'local',
                    '--keep-going', '--failures-file', str(path)]
            with patch('sys.argv', argv), patch.object(checks, 'run_group', return_value=['first', 'second']):
                self.assertEqual(checks.main(), 1)
            self.assertEqual(path.read_text(), 'first\nsecond\n')


class QuoteTodoSourceTests(unittest.TestCase):
    """在临时源码副本中破坏关键连接，确认检查仍能拒绝真实漂移。"""

    def run_checker(self, page_change=None, todo_change=None):
        page_path = 'frontend/src/modules/quote/QuoteDetailPage.tsx'
        todo_path = 'frontend/src/shared/quote-todo.ts'
        with tempfile.TemporaryDirectory(prefix='crm-quote-source-') as directory:
            root = Path(directory)
            for name, change in [('ops/check_quote_todo_source.py', None),
                                 (page_path, page_change), (todo_path, todo_change)]:
                text = (checks.ROOT / name).read_text(encoding='utf-8')
                if change:
                    old, new = change
                    self.assertIn(old, text, '负向夹具必须命中真实源码')
                    text = text.replace(old, new)
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8')
            return subprocess.run([sys.executable, str(root / 'ops/check_quote_todo_source.py')],
                                  capture_output=True, text=True)

    def assert_rejected(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message, result.stdout)

    def test_current_cost_navigation_is_valid(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_cost_input_is_rejected(self):
        self.assert_rejected(self.run_checker(page_change=(
            'costProblems: detail.cost_problems', 'costProblems: undefined')),
            "'cost_incomplete' 声称的判据")

    def test_missing_cost_todo_is_rejected(self):
        self.assert_rejected(self.run_checker(todo_change=(
            "key: 'cost_incomplete'", "key: 'removed_cost_todo'")), '会漏报')

    def test_missing_cost_anchor_is_rejected(self):
        self.assert_rejected(self.run_checker(page_change=(
            'id="quote-cost-status"', 'id="removed-cost-status"')), '但页面上没有这个 id')

    def test_hidden_cost_status_is_rejected(self):
        self.assert_rejected(self.run_checker(page_change=(
            '{(detail.cost_problems ?? []).length > 0 && (', '{false && (')),
            '成本未完整确认的提示块')

    def test_missing_anchor_link_is_rejected(self):
        self.assert_rejected(self.run_checker(page_change=(
            '{todo.anchor && (', '{false && (')), '没有接入真实的点击跳转')

    def test_broken_scroll_handler_is_rejected(self):
        self.assert_rejected(self.run_checker(page_change=(
            'document.getElementById(todo.anchor as string)?.scrollIntoView(',
            'document.getElementById("wrong-anchor")?.scrollIntoView(')), '没有接入真实的点击跳转')

    def test_wrong_cost_navigation_description_is_rejected(self):
        self.assert_rejected(self.run_checker(todo_change=(
            "action: '查看成本缺项'", "action: '不存在的成本操作'")), '成本导航说明或目标锚点')

    def test_other_actions_still_require_real_buttons(self):
        self.assert_rejected(self.run_checker(page_change=(
            '提交审批', '已移除提交入口')), '但页面上没有这个文案的按钮')


if __name__ == '__main__':
    unittest.main()
