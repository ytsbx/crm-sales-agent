"""钉钉扫码登录回归（不依赖网络；需要 PostgreSQL 在跑）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_dingtalk_login.py

## 覆盖

1. **未就绪不许自称可用**：凭据缺 / 外部调用总闸关着 → 就绪判据为假；
2. **身份只由服务端换**：授权码 → 用户级令牌 → unionId → userid 全程走替身，
   验的是"有没有偷偷相信调用方给的东西"；
3. **拒绝理由分开报**：不在通讯录 / 没拿到 unionId / 没绑定 / 对上一堆账号 /
   账号停用 —— 五种情况各有说法，不混成一句"登录失败"；
4. **不取第一个**：一个钉钉号对上多个系统账号时**明确拒绝**
   （取第一个 = 把别人的账号登进来，与「重名一律不猜」同一条纪律）；
5. **state 一次性**：用过即废、过期即废、不认识即拒（防重放 / 防伪造回调）；
6. **ticket 一次性**：同上，且过期后取不出来。

## 为什么用替身

真调钉钉要企业凭据、会打扰真实的人，而且**授权码只能用一次**（重跑必然失败），
没法反复验证。本套件验的是"换取身份的规矩"，与网络无关，所以把 client 换成假的。
替身打在 `service.get_client` —— 也就是 service **真正读的那一处**，
不是打在 client 类上（那样 service 换个取法就绕过去了）。
"""

import asyncio
import sys

from _test_support import require_isolated_db

require_isolated_db()
FAILURES = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


class FakeDingTalkLogin:
    """假的钉钉登录接口：只回答"这个码是谁"。

    `userid` / `unionid` / `userinfo` 由构造参数摆出各种"换不出人"的局面。
    """

    def __init__(self, *, userid='dtlogin_ok', unionid='union_ok', userinfo=None):
        self._userid = userid
        self._unionid = unionid
        self._userinfo = userinfo
        #: 记下每次被问到什么 —— 验"到底有没有真的去换"靠它
        self.calls = []

    async def exchange_user_token(self, code):
        self.calls.append(('exchange', code))
        return 'fake-user-token'

    async def get_login_userinfo(self, user_access_token):
        self.calls.append(('me', user_access_token))
        if self._userinfo is not None:
            return self._userinfo
        return {'unionId': self._unionid, 'nick': '测试用户'}

    async def userid_by_unionid(self, unionid):
        self.calls.append(('byunionid', unionid))
        return self._userid


PREFIX = '__dtlogin_'


async def main():
    from unittest.mock import patch

    from sqlalchemy import text

    from app.core.config import settings as app_settings
    from app.core.database import SessionLocal
    from app.modules.dingtalk import service as dt_service
    from app.modules.user.model import User

    async def cleanup():
        """只清本套件自己造的夹具（按前缀），绝不动别人的数据。"""
        async with SessionLocal() as s:
            await s.execute(
                text('delete from users where username like :p'), {'p': PREFIX + '%'}
            )
            await s.commit()

    async def add_user(username, *, dingtalk_userid=None, status='active'):
        async with SessionLocal() as s:
            row = User(
                name=username,
                username=PREFIX + username,
                password_hash='x',
                status=status,
                dingtalk_userid=dingtalk_userid,
            )
            s.add(row)
            await s.commit()
            return row.id

    await cleanup()
    try:
        print('1) 就绪判据：只看登录自己那把闸（与审批总闸互不影响）')
        saved = (
            app_settings.dingtalk_app_key,
            app_settings.dingtalk_app_secret,
            app_settings.dingtalk_push_off,
            app_settings.dingtalk_login_off,
        )
        try:
            app_settings.dingtalk_app_key = ''
            check('凭据缺失 → 未就绪', app_settings.dingtalk_login_ready, False)

            app_settings.dingtalk_app_key = 'k'
            app_settings.dingtalk_app_secret = 's'
            app_settings.dingtalk_login_off = True
            check('凭据齐但登录闸关着 → 未就绪', app_settings.dingtalk_login_ready, False)

            app_settings.dingtalk_login_off = False
            check('凭据齐且登录闸开 → 就绪', app_settings.dingtalk_login_ready, True)

            # ⭐ 这条是「只开登录、不开放审批」的核心：
            # 审批总闸关着，登录照样就绪。
            app_settings.dingtalk_push_off = True
            check(
                '审批总闸关着也照样就绪（只开登录）',
                app_settings.dingtalk_login_ready,
                True,
            )
            # 反过来：总闸开着但登录闸关着 → 仍不许登录。
            app_settings.dingtalk_push_off = False
            app_settings.dingtalk_login_off = True
            check(
                '审批总闸开着但登录闸关着 → 仍不许登录',
                app_settings.dingtalk_login_ready,
                False,
            )

            print('1b) 闸门真的拦得住（不只改了就绪判据）')
            from app.modules.dingtalk.client import DingTalkClient, DingTalkDisabled

            api = DingTalkClient()
            app_settings.dingtalk_login_off = True
            app_settings.dingtalk_push_off = True  # 故意两把都关
            try:
                await api.exchange_user_token('whatever')
                check_true('登录闸关着时换令牌应被拦', False, '居然没抛异常')
            except DingTalkDisabled as exc:
                check_true(
                    '登录闸关着时换令牌被拦，且报的是登录闸',
                    'DINGTALK_LOGIN_OFF' in str(exc),
                    str(exc),
                )

            print('1c) 成员同步归登录闸（只开登录时也能把人拉下来）')
            # 闸门层面：登录闸开着时，登录这一组不该被审批总闸牵连。
            app_settings.dingtalk_login_off = False
            app_settings.dingtalk_push_off = True  # 审批总闸仍关着
            try:
                api._require_login()
                check_true('总闸关但登录闸开：登录这组放行', True)
            except DingTalkDisabled as exc:
                check_true('总闸关但登录闸开：不该被拦', False, str(exc))

            # 服务层：登录闸关着时，同步如实回报 disabled（**根本没去拉通讯录**）。
            app_settings.dingtalk_login_off = True
            async with SessionLocal() as s:
                res = await dt_service.sync_users(s)
            check('登录闸关着 → 同步回报 disabled', res.get('disabled'), True)
            check('且不假装成功（人数为 0）', res.get('total'), 0)

            # 拉通讯录那一步也挂登录闸：闸关着时连请求都不该发出去。
            try:
                await api.list_department_users(1)
                check_true('闸关着时拉成员应被拦', False, '居然没抛异常')
            except DingTalkDisabled as exc:
                check_true(
                    '闸关着时拉成员被拦（登录闸）',
                    'DINGTALK_LOGIN_OFF' in str(exc),
                    str(exc),
                )
        finally:
            (
                app_settings.dingtalk_app_key,
                app_settings.dingtalk_app_secret,
                app_settings.dingtalk_push_off,
                app_settings.dingtalk_login_off,
            ) = saved

        print('2) 换取身份：正常的链路能对出人')
        ok_id = await add_user('ok', dingtalk_userid='dtlogin_ok')
        fake = FakeDingTalkLogin(userid='dtlogin_ok')
        with patch.object(dt_service, 'get_client', lambda: fake):
            async with SessionLocal() as s:
                user, reason = await dt_service.resolve_login_user(s, code='code-1')
        check('reason', reason, 'ok')
        check('对出的人', user.id if user else None, ok_id)
        check('确实走了三步', [c[0] for c in fake.calls], ['exchange', 'me', 'byunionid'])

        print('3) 拒绝理由要分开（五种各一句，不混成"登录失败"）')
        async with SessionLocal() as s:
            # 不在通讯录：unionId 换不出 userid
            with patch.object(
                dt_service, 'get_client', lambda: FakeDingTalkLogin(userid=None)
            ):
                _, r1 = await dt_service.resolve_login_user(s, code='c')
            check('不在通讯录', r1, 'not_in_contact')

            # 没拿到 unionId
            with patch.object(
                dt_service,
                'get_client',
                lambda: FakeDingTalkLogin(userinfo={'nick': '无标识'}),
            ):
                _, r2 = await dt_service.resolve_login_user(s, code='c')
            check('没 unionId', r2, 'no_unionid')

            # 通讯录里有、CRM 里没绑
            with patch.object(
                dt_service,
                'get_client',
                lambda: FakeDingTalkLogin(userid='dtlogin_nobody'),
            ):
                _, r3 = await dt_service.resolve_login_user(s, code='c')
            check('没绑定', r3, 'not_bound')

        print('4) 一个钉钉号对上多个账号 → 明确拒绝，不取第一个')
        await add_user('dup1', dingtalk_userid='dtlogin_dup')
        await add_user('dup2', dingtalk_userid='dtlogin_dup')
        with patch.object(
            dt_service, 'get_client', lambda: FakeDingTalkLogin(userid='dtlogin_dup')
        ):
            async with SessionLocal() as s:
                _, r4 = await dt_service.resolve_login_user(s, code='c')
        check('对上一堆账号', r4, 'ambiguous')

        print('5) 停用账号不许进')
        await add_user('off', dingtalk_userid='dtlogin_off', status='disabled')
        with patch.object(
            dt_service, 'get_client', lambda: FakeDingTalkLogin(userid='dtlogin_off')
        ):
            async with SessionLocal() as s:
                _, r5 = await dt_service.resolve_login_user(s, code='c')
        check('账号停用', r5, 'disabled')

        print('6) state：一次性（用过即废 / 不认识即拒 / 过期即废）')
        st = dt_service.issue_login_state()
        check('刚发的 state 认', dt_service.consume_login_state(st), True)
        check('同一个再用一次 → 拒（防重放）', dt_service.consume_login_state(st), False)
        check('瞎编的 state → 拒', dt_service.consume_login_state('nope-nope'), False)
        check('空 state → 拒', dt_service.consume_login_state(None), False)
        st2 = dt_service.issue_login_state()
        dt_service._login_states[st2] = 0.0  # 直接改成"已过期"
        check('过期的 state → 拒', dt_service.consume_login_state(st2), False)

        print('7) ticket：一次性 + 过期即废')
        tk = dt_service.issue_login_ticket('token-abc')
        check('刚发的 ticket 能取回令牌', dt_service.consume_login_ticket(tk), 'token-abc')
        check('同一个再用 → None', dt_service.consume_login_ticket(tk), None)
        check('瞎编的 ticket → None', dt_service.consume_login_ticket('nope'), None)
        tk2 = dt_service.issue_login_ticket('token-def')
        dt_service._login_tickets[tk2] = ('token-def', 0.0)
        check('过期的 ticket → None', dt_service.consume_login_ticket(tk2), None)
    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} 项失败：')
        for name in FAILURES:
            print(f'  - {name}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    asyncio.run(main())
