"""通知分级与日报投递回归（文档 §11.4 验收 24）。

跑法（后端要在 8000 跑着——策略读写走 HTTP，夹具走库）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_notification_levels.py

## 场景 24 的原话

| 24 | 主管一天收到大量业务事件 | 每条真实事件**可查且去重**；按最终批准的逐次或分级策略投递，
**紧急项不被日报延误**。 |

逐条落成断言：

1. 默认策略不改行为：`default_level=normal`、`by_type` 为空 → 一律即时推（与分级上线前一致）；
2. 分级落到行上：按策略给类型定级，`notify(level_override=...)` 能覆盖策略；
3. **日报级不占即时通道**：`dispatch_pending` 不碰 `level=digest` 的待发行；
4. **紧急项不被日报延误**：`urgent` 走即时通道，且不会被日报收走（`digest_at` 为空）；
5. **聚合成一条**：3 条日报事件 → 1 条消息（`messages=1` / `items=3`），
   正文逐条列出；
6. **每条事件仍可查**：日报不改写、不删除任何通知行，标题内容原样；
7. 策略校验：非法级别被 422 拒绝（写错了会直接表现为"通知不发/乱发"）。

夹具标题一律带 `CHKL` 前缀，跑完即清；分级策略跑完**还原成进来时的样子**
（它是全局设置，不能给后面的用例留个 task=digest 的坑）。
"""

import asyncio
import os
import json
import sys
import urllib.error
import urllib.request

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES = []
BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
PREFIX = 'CHKL'


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or '{}')


def login() -> str:
    _, res = call('POST', '/auth/login', body={'username': 'admin', 'password': 'admin123'})
    if res.get('code') != 0:
        raise SystemExit('登录失败：后端没在 8000 跑？')
    return res['data']['access_token']


async def cleanup():
    async with SessionLocal() as s:
        await s.execute(
            text("delete from notifications where title like :p"), {'p': f'{PREFIX}%'}
        )
        await s.commit()


async def main():
    from app.modules.notification import service as notif
    from app.modules.notification.model import (
        LEVEL_DIGEST,
        LEVEL_NORMAL,
        LEVEL_URGENT,
        Notification,
    )
    from app.modules.user.model import User

    token = login()
    await cleanup()

    # 复核用：把进来时的策略存下来，最后还原
    _, res = call('GET', '/notifications/level-policy', token=token)
    saved_policy = {
        'default_level': res['data']['default_level'],
        'by_type': res['data']['by_type'],
    }
    print('=== 0. 默认策略：不擅自改变投递方式 ===')
    check('默认级别为 normal（与分级上线前一致）', saved_policy['default_level'], 'normal')
    check('默认没有指定任何 digest 类型', saved_policy['by_type'], {})

    forced_channels = {
        'inapp_enabled': True,
        'wecom_enabled': True,
        'wecom_events': {'task': True, 'approval': True, 'payment': True},
    }

    try:
        print('=== 1. 策略校验 ===')
        status, res = call(
            'PUT',
            '/notifications/level-policy',
            token=token,
            body={'default_level': 'normal', 'by_type': {'task': 'urgentt'}},
        )
        check('非法级别被拒', status, 422)
        status, res = call(
            'PUT',
            '/notifications/level-policy',
            token=token,
            body={'default_level': 'not_a_level', 'by_type': {}},
        )
        check('非法默认级别被拒', status, 422)

        print('=== 2. 按策略定级 ===')
        status, res = call(
            'PUT',
            '/notifications/level-policy',
            token=token,
            body={
                'default_level': 'normal',
                'by_type': {'task': 'digest', 'approval': 'urgent'},
            },
        )
        check('策略保存成功', res.get('code'), 0)

        async with SessionLocal() as s:
            admin = (
                await s.execute(select(User).where(User.username == 'admin'))
            ).scalars().one()
            policy = await notif.level_policy(s)
            check('task 解析为日报级', notif.resolve_level(policy, 'task'), LEVEL_DIGEST)
            check('approval 解析为紧急级', notif.resolve_level(policy, 'approval'), LEVEL_URGENT)
            check('没配的类型走默认级', notif.resolve_level(policy, 'payment'), LEVEL_NORMAL)
            check(
                '策略里没有的级别值一律退回默认级（不让脏配置把通知吞掉）',
                notif.resolve_level({'default_level': 'normal', 'by_type': {'x': 'bogus'}}, 'x'),
                LEVEL_NORMAL,
            )

            digest_rows = []
            for idx in range(3):
                digest_rows.append(
                    await notif.notify(
                        s,
                        user_id=admin.id,
                        type_='task',
                        title=f'{PREFIX}-日报事件{idx}',
                        content='夹具',
                        channel_settings_override=forced_channels,
                    )
                )
            urgent_row = await notif.notify(
                s,
                user_id=admin.id,
                type_='approval',
                title=f'{PREFIX}-紧急停线',
                channel_settings_override=forced_channels,
            )
            normal_row = await notif.notify(
                s,
                user_id=admin.id,
                type_='payment',
                title=f'{PREFIX}-普通事件',
                channel_settings_override=forced_channels,
            )
            # 调用点明确知道这条最急时可以覆盖策略
            override_row = await notif.notify(
                s,
                user_id=admin.id,
                type_='task',
                title=f'{PREFIX}-覆盖为紧急',
                channel_settings_override=forced_channels,
                level_override=LEVEL_URGENT,
            )
            await s.commit()
            check('task 落的级别', digest_rows[0].level, LEVEL_DIGEST)
            check('approval 落的级别', urgent_row.level, LEVEL_URGENT)
            check('payment 落的级别', normal_row.level, LEVEL_NORMAL)
            check('level_override 生效', override_row.level, LEVEL_URGENT)
            check_true(
                '这几条都排在企微投递队列里',
                all(r.wecom_status == 'pending' for r in [*digest_rows, urgent_row, normal_row, override_row]),
            )

        print('=== 3. 日报级不占即时通道，紧急项不被日报延误 ===')
        sent_result = await notif.dispatch_pending(
            None, only_ids={urgent_row.id, normal_row.id, override_row.id}
        )
        check('即时通道处理了 3 条（紧急 + 普通）', sent_result['attempted'], 3)
        async with SessionLocal() as s:
            async def reload(rid):
                return await s.get(Notification, rid)

            for row in digest_rows:
                fresh = await reload(row.id)
                check(f'{row.title} 仍未被即时通道取走', fresh.wecom_status, 'pending')
            fresh_urgent = await reload(urgent_row.id)
            check_true(
                '紧急项已离开待投递（本机未配企微，标为未投递也算已处理）',
                fresh_urgent.wecom_status != 'pending',
                f"wecom_status={fresh_urgent.wecom_status}",
            )
            check('紧急项不是日报发的', fresh_urgent.digest_at, None)

        print('=== 4. 日报把多条聚合成一条 ===')
        async with SessionLocal() as s:
            pending_digest = (
                await s.execute(
                    select(Notification).where(
                        Notification.id.in_([r.id for r in digest_rows])
                    )
                )
            ).scalars().all()
            title, description = notif.build_digest(pending_digest)
            check('日报标题给出条数', title, f'业务日报（{len(digest_rows)} 条）')
            check('正文逐条列出', len([ln for ln in description.splitlines() if ln.strip()]), 3)

            before = (
                await s.execute(
                    text("select count(*) from notifications where title like :p"),
                    {'p': f'{PREFIX}%'},
                )
            ).scalar_one()

        digest_result = await notif.send_digest()
        check('每人一条消息', digest_result['messages'], 1)
        check('覆盖 3 条事件', digest_result['items'], len(digest_rows))

        print('=== 5. 每条事件仍然逐条可查（日报不改写、不删除） ===')
        async with SessionLocal() as s:
            after = (
                await s.execute(
                    text("select count(*) from notifications where title like :p"),
                    {'p': f'{PREFIX}%'},
                )
            ).scalar_one()
            check('通知行数没变', after, before)
            for idx, row in enumerate(digest_rows):
                fresh = await s.get(Notification, row.id)
                check(f'第 {idx} 条的标题原样', fresh.title, f'{PREFIX}-日报事件{idx}')
                check_true(
                    f'第 {idx} 条记下了它是日报发的',
                    fresh.digest_at is not None,
                    f"digest_at={fresh.digest_at}",
                )
                check_true(
                    f'第 {idx} 条已离开待投递队列（否则下次日报会重复带上）',
                    fresh.wecom_status != 'pending',
                    f"wecom_status={fresh.wecom_status}",
                )

        print('=== 6. 手动触发日报接口（与定时任务同一个函数） ===')
        status, res = call('POST', '/notifications/digest/run', token=token, body={})
        check('接口可用', res.get('code'), 0)
        check_true(
            '再跑一次不会重复处理已经发过的（不会把同一批再发一遍）',
            res['data']['items'] == 0,
            f"items={res['data']['items']}",
        )
    finally:
        await cleanup()
        call('PUT', '/notifications/level-policy', token=token, body=saved_policy)

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('通知分级与日报回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
