"""通知「投递失败 → 自动重试 → 真的补发成功」端到端演练。

**这是第六批第 8 条的「验到底」补课。** 与 `check_notification_retry.py` 的分工：

  - 那个套件验的是**入选谓词 / 人工补投不进业务去重键 / 概览口径**，
    全是只读断言加直接调服务函数，**一个 HTTP 请求都不发**；
  - 这个脚本押在真链路上：真数据库 + 真服务层 + **真 HTTP**（打在进程内的假企微上），
    把「首次投递被企微拒 → 退避期内不重试 → 退避到期后重试通道把它补出去」
    整条走一遍，并断言最终真的发出去了。

跑法（**必须**给一次性隔离库；脚本会拒绝跑在名字不含 test 的库上）：

    cd backend
    DATABASE_URL="$(cat /tmp/crm_test_url.txt)" PYTHONPATH=. \\
        .venv/bin/python scripts/check_notification_retry_e2e.py

安全性：脚本先在 127.0.0.1:9101 起一个**进程内**的假企微服务，并把
WECOM_API_BASE / WECOM_CORP_ID / WECOM_CONTACT_SECRET / WECOM_AGENT_ID
全部改写成假值**之后**才 import 应用（进程环境变量优先于 .env），
所以**不可能**有真实消息发出去。
夹具只碰标题以 CHK-E2E-RETRY 开头的行，跑完即清，不动库里其它通知。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# ---------------------------------------------------------------------------
# 关键：以下环境变量必须在 import 任何 app.* 之前写好。
# pydantic-settings 的优先级是「进程环境变量 > .env」，所以这里能把 .env 里
# 的真实企微凭据盖掉——这正是"演练不会发真实消息"的保证。
# ---------------------------------------------------------------------------
FAKE_WECOM_PORT = 9101
os.environ['WECOM_API_BASE'] = f'http://127.0.0.1:{FAKE_WECOM_PORT}'
os.environ['WECOM_CORP_ID'] = 'ww-e2e-fake'
os.environ['WECOM_CONTACT_SECRET'] = 'e2e-fake-secret'
os.environ['WECOM_AGENT_ID'] = '1000002'
os.environ['WECOM_PUSH_OFF'] = '0'   # 默认是 1（推送总闸关闭）：打开才有真实投递动作
os.environ['SCHEDULER_ENABLED'] = 'false'

TITLE_PREFIX = 'CHK-E2E-RETRY'

FAILURES: list[str] = []
SENT_BODIES: list[dict] = []
_SEND_CALLS = {'n': 0}


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


class _FakeWeComHandler(BaseHTTPRequestHandler):
    """只认两个接口：取 token（GET）和发消息（POST）。第一次发消息**故意拒绝**。"""

    def _reply(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - gettoken 走 GET
        if self.path.startswith('/cgi-bin/gettoken'):
            self._reply({
                'errcode': 0, 'errmsg': 'ok',
                'access_token': 'e2e-fake-token', 'expires_in': 7200,
            })
        else:
            self._reply({'errcode': 0, 'errmsg': 'ok'})

    def do_POST(self):  # noqa: N802 - message/send 走 POST
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length)
        if self.path.startswith('/cgi-bin/message/send'):
            try:
                SENT_BODIES.append(json.loads(raw or b'{}'))
            except json.JSONDecodeError:
                SENT_BODIES.append({'_raw': raw.decode('utf-8', 'replace')})
            _SEND_CALLS['n'] += 1
            if _SEND_CALLS['n'] == 1:
                # 第一次「企微拒绝」——这正是要复现的投递失败
                self._reply({
                    'errcode': 40001,
                    'errmsg': 'invalid credential（演练故意造的首次失败）',
                })
            else:
                self._reply({'errcode': 0, 'errmsg': 'ok'})
            return
        self._reply({'errcode': 0, 'errmsg': 'ok'})

    def log_message(self, *args):  # 静音，别刷屏
        pass


def start_fake_wecom() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(('127.0.0.1', FAKE_WECOM_PORT), _FakeWeComHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main() -> int:
    from sqlalchemy import select, text

    from app.core import scheduler
    from app.core.database import SessionLocal
    from app.modules.notification import service as ns
    from app.modules.notification.model import Notification
    from app.modules.settings.model import SystemSetting
    from app.modules.user.model import User

    db_url = os.environ.get('DATABASE_URL', '')
    if 'test' not in db_url.lower():
        print(f'拒绝执行：DATABASE_URL 不像一次性测试库（{db_url!r}），'
              '本演练会清夹具、要求先灌种子，别对着开发库跑。')
        return 2

    server = start_fake_wecom()
    print(f'假企微已就位：http://127.0.0.1:{FAKE_WECOM_PORT}（第一次发消息必失败）')
    print()

    title = f'{TITLE_PREFIX} 企微补发演练'

    async with SessionLocal() as s:
        # 0) 清上次跑剩的
        await s.execute(
            text('delete from notifications where title like :p'), {'p': f'{TITLE_PREFIX}%'}
        )
        await s.commit()

        # 1) 打开企微渠道 —— 等价于管理员在「系统设置 → 通知」里把开关点开
        ch = (
            await s.execute(
                select(SystemSetting).where(SystemSetting.key == 'notification_channels')
            )
        ).scalar_one_or_none()
        original_value = dict(ch.value) if (ch is not None and ch.value) else None
        value = dict(original_value or {})
        value['inapp_enabled'] = True
        value['wecom_enabled'] = True
        events = dict(value.get('wecom_events') or {})
        events['task'] = True
        value['wecom_events'] = events
        if ch is None:
            s.add(SystemSetting(key='notification_channels', value=value))
        else:
            ch.value = value

        # 2) 找一个有效用户，确保它绑了企微 userid
        #    （没绑的话会被判 skipped 而不是 failed，就走不到"失败重试"这条路）
        user = (
            await s.execute(select(User).where(User.username == 'admin'))
        ).scalar_one_or_none()
        check_true('隔离库里有名为 admin 的用户', user is not None)
        if user is None:
            await s.rollback()
            server.shutdown()
            return 1
        user.wecom_userid = user.wecom_userid or f'e2e-{user.id}'
        await s.commit()
        user_id = user.id
        print(f'夹具用户 id={user_id}，企微 userid={user.wecom_userid}')
        print()

        # 3) 落一条待投递的通知（走业务落库的那个函数，不直接插表）
        await ns.notify(
            s, user_id=user_id, type_='task', title=title,
            content='演练：这条要先被企微拒一次，再由重试通道补发出去',
        )
        await s.commit()
        row = (
            await s.execute(select(Notification).where(Notification.title == title))
        ).scalars().one()
        nid = row.id
        check('落库后是 pending（等着投递）', row.wecom_status, 'pending')

        print()
        print('=== 1. 首次投递：企微返回错误 ===')
        sent_before = len(SENT_BODIES)
        result = await ns.dispatch_pending(s)
        check('这一轮投递处理了 1 条', result['attempted'], 1)
        check('其中失败 1 条', result['failed'], 1)
        check('确实发出了 HTTP 请求（不是空跑）', len(SENT_BODIES) - sent_before, 1)
        await s.refresh(row)
        check('状态转 failed', row.wecom_status, 'failed')
        check('失败次数 = 1', row.wecom_attempts, 1)
        check_true(
            '错误里留下了企微的错误码', '40001' in (row.wecom_error or ''),
            row.wecom_error or '',
        )
        check_true(
            '已排好下次重试时间', row.wecom_next_retry_at is not None,
            str(row.wecom_next_retry_at),
        )

        print()
        print('=== 2. 退避还没走完：重试通道不该再打企微 ===')
        sent_before = len(SENT_BODIES)
        await scheduler.run_notification_retry_job()
        check('退避期内一条都没发', len(SENT_BODIES) - sent_before, 0)
        await s.refresh(row)
        check('状态仍然是 failed', row.wecom_status, 'failed')
        check('失败次数没被重复加', row.wecom_attempts, 1)

        print()
        print('=== 3. 退避到期：重试通道把它补发出去 ===')
        await s.execute(
            text("update notifications set wecom_next_retry_at = now() - interval '1 minute'"
                 ' where id = :i'),
            {'i': nid},
        )
        await s.commit()
        sent_before = len(SENT_BODIES)
        await scheduler.run_notification_retry_job()
        check('企微这次收到了（补发真的出去了）', len(SENT_BODIES) - sent_before, 1)
        await s.refresh(row)
        check('状态转 sent', row.wecom_status, 'sent')
        check('尝试次数累到 2', row.wecom_attempts, 2)
        check_true('记下了发出时间', row.wecom_sent_at is not None, str(row.wecom_sent_at))
        check_true(
            '重试时间已清空（不会再排）', row.wecom_next_retry_at is None,
            str(row.wecom_next_retry_at),
        )
        # 只看"这一轮新增的"请求体：拿 [-1]（最后一次）会在补发失败时读到上一轮的残留，
        # 反向验证时这条会假绿——所以必须按本轮切片。
        new_bodies = SENT_BODIES[sent_before:]
        check_true(
            '补发的就是同一条内容',
            any(title in json.dumps(b, ensure_ascii=False) for b in new_bodies),
            str(new_bodies)[:140],
        )

        print()
        print('=== 4. 到次数上限：停在"等人工"，不再自动重试 ===')
        exhausted = Notification(
            user_id=user_id, type='task', title=f'{title}-已到上限',
            content='这条不该被自动重试', channel='both',
            wecom_status='failed', wecom_attempts=99, wecom_next_retry_at=None,
        )
        s.add(exhausted)
        await s.commit()
        sent_before = len(SENT_BODIES)
        await scheduler.run_notification_retry_job()
        check('到上限的没被自动重试', len(SENT_BODIES) - sent_before, 0)
        await s.refresh(exhausted)
        check('它仍停在 failed 等人工', exhausted.wecom_status, 'failed')

        print()
        print('=== 5. 人工补投：和界面同一个函数，也能补出去 ===')
        sent_before = len(SENT_BODIES)
        queued = await ns.requeue_for_redispatch(s, ids=[exhausted.id])
        check('重新排队 1 条', queued['requeued'], 1)
        result = await ns.dispatch_pending(s, only_ids={exhausted.id})
        check('补投成功 1 条', result['sent'], 1)
        check('企微收到了这一条', len(SENT_BODIES) - sent_before, 1)
        await s.refresh(exhausted)
        check('人工补投后状态是 sent', exhausted.wecom_status, 'sent')

        # 清理夹具
        await s.execute(
            text('delete from notifications where title like :p'), {'p': f'{TITLE_PREFIX}%'}
        )
        await s.commit()

        # 还原通知渠道设置：演练开的那个开关不该留在库里。
        # 逐字段赋值（而不是原地改 dict），SQLAlchemy 才认得出这是变更。
        if ch is None:
            leftover = (
                await s.execute(
                    select(SystemSetting).where(SystemSetting.key == 'notification_channels')
                )
            ).scalar_one_or_none()
            if leftover is not None:
                await s.delete(leftover)
        else:
            ch.value = original_value
        await s.commit()

    server.shutdown()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('通知失败补发端到端演练 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
