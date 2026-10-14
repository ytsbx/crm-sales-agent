"""钉钉人员绑定回归（不依赖网络；需要 PostgreSQL 在跑）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_dingtalk_user_bind.py

## 覆盖

1. **匹配顺序**：手机号命中优先，姓名只做兜底且**唯一才认**；
2. **重名不猜**：同名候选多于一个 → 不绑，列入未对上并写清原因；
3. **手机号撞多个也不猜**：两个账号同一个手机号 → 不绑；
4. **不自动建账号**：钉钉里有人、CRM 里没账号 → 只列出来，数据库里人数不变；
5. **已绑定的不被改绑**（改绑等于换掉一个人的登录身份）；
6. **部门树去重**：同一个人挂在两个部门下，只算一次；
7. **登录闸关着时一次请求都不发**（不是"发出去被拒"，是压根没发）。
   （2026-10-14 起，这一步判的是登录闸 `DINGTALK_LOGIN_OFF` 而不是审批总闸
   —— 它属于"登录那一组"。）

## 为什么用替身

真调钉钉要企业凭据 + 通讯录权限，而且**钉钉那边看得到调用、会打扰人**。
本套件要验的是"对上的规矩"，与网络无关，所以把 client 换成假的。
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


class FakeDingTalk:
    """假的钉钉通讯录：只回答"有哪些子部门"和"部门里有哪些人"。"""

    def __init__(self, departments, members_by_dept):
        self._departments = departments
        self._members = members_by_dept
        #: 记下每次被问到什么 —— 验"登录闸关着时一次都没发"要靠它
        self.calls = []

    async def list_sub_departments(self, dept_id):
        self.calls.append(('departments', dept_id))
        return list(self._departments.get(dept_id, []))

    async def list_department_users(self, dept_id, *, cursor=0, size=100):
        self.calls.append(('users', dept_id))
        rows = list(self._members.get(dept_id, []))
        # 一页给完；has_more 是翻页的收尾判据（不是"条数 < size"）
        return {'list': rows, 'has_more': False, 'next_cursor': 0}


PREFIX = '__dtcheck_'


async def main():
    from unittest.mock import patch

    from sqlalchemy import func, select, text

    from app.core.config import settings as app_settings
    from app.core.database import SessionLocal
    from app.modules.dingtalk import service as dt_service
    from app.modules.user.model import User

    async def cleanup():
        """按前缀清掉**本套件自己造的**夹具，绝不动别人的数据。"""
        async with SessionLocal() as s:
            await s.execute(
                text('delete from users where username like :p'), {'p': PREFIX + '%'}
            )
            await s.commit()

    await cleanup()
    baseline_total = 0
    # 成员同步现在挂**登录闸**（2026-10-14 从审批总闸拆出来）：它做的是
    # "把人跟钉钉号对上"，属于登录的前置步骤。所以下面控制放行/拦截的是这一把。
    original_login_off = app_settings.dingtalk_login_off

    print()
    print('=== 1. 造夹具 ===')
    async with SessionLocal() as s:
        s.add_all(
            [
                # 手机号能对上（甲）
                User(name='甲', username=PREFIX + 'a', password_hash='x',
                     mobile='13800000001', status='active'),
                # 手机号为空，只能靠姓名（乙）
                User(name='乙', username=PREFIX + 'b', password_hash='x',
                     mobile=None, status='active'),
                # 重名两个：谁也判不了（丙）
                User(name='丙', username=PREFIX + 'c1', password_hash='x',
                     mobile=None, status='active'),
                User(name='丙', username=PREFIX + 'c2', password_hash='x',
                     mobile=None, status='active'),
                # 两个账号同一个手机号：也不判（丁）
                User(name='丁', username=PREFIX + 'd1', password_hash='x',
                     mobile='13900000001', status='active'),
                User(name='丁', username=PREFIX + 'd2', password_hash='x',
                     mobile='13900000001', status='active'),
                # 已经绑过钉钉的（己）：这次不能被改
                User(name='己', username=PREFIX + 'f', password_hash='x',
                     mobile='13600000001', status='active',
                     dingtalk_userid='dt_old'),
            ]
        )
        await s.commit()
        baseline_total = (
            await s.execute(select(func.count()).select_from(User))
        ).scalar_one()
    check_true('夹具已建（7 个账号）', baseline_total > 0, f'库中用户总数 {baseline_total}')

    fake = FakeDingTalk(
        departments={1: [{'dept_id': 2, 'name': '子部门'}], 2: []},
        members_by_dept={
            1: [
                {'userid': 'dt_a', 'name': '甲', 'mobile': '13800000001'},
                {'userid': 'dt_b', 'name': '乙'},                      # 无手机号，靠姓名
                {'userid': 'dt_c', 'name': '丙'},                      # 重名，判不了
                {'userid': 'dt_d1', 'name': '丁', 'mobile': '13900000001'},  # 手机号撞两个
                {'userid': 'dt_e', 'name': '戊', 'mobile': '13700000009'},   # CRM 没这人
                {'userid': 'dt_old', 'name': '己', 'mobile': '13600000001'},  # 已绑定
            ],
            # 同一个人又挂在子部门下：**必须只算一次**（否则他会跟自己撞）
            2: [
                {'userid': 'dt_a', 'name': '甲', 'mobile': '13800000001'},
            ],
        },
    )

    print()
    print('=== 2. 同步（替身通讯录，放行登录闸）===')
    app_settings.dingtalk_login_off = False
    with patch.object(dt_service, 'get_client', return_value=fake):
        async with SessionLocal() as s:
            result = await dt_service.sync_users(s)
            # ⚠️ service **只 flush 不提交**：提交归调用方，好让"审计 + 业务改动"
            # 落在同一次事务里（router 就是这么做的）。套件在这里就是调用方，
            # 所以必须自己提交 —— 不提交的话，下面另开 session 读到的是空的。
            await s.commit()

    check('钉钉人数（两个部门同一人只算一次）', result['total'], 6)
    check('本次绑定', result['bound'], 2)
    check('之前已绑定', result['already_bound'], 1)
    check('未对上条数', len(result['unmatched']), 3)
    check(
        '未对上的原因（三种要分开报）',
        sorted(item['reason'] for item in result['unmatched']),
        ['ambiguous_mobile', 'ambiguous_name', 'no_account'],
    )

    print()
    print('=== 3. 落库结果 ===')
    async with SessionLocal() as s:
        rows = {
            row.username: row
            for row in (
                await s.execute(select(User).where(User.username.like(PREFIX + '%')))
            ).scalars().all()
        }
    check('甲（手机号命中）绑上了', rows[PREFIX + 'a'].dingtalk_userid, 'dt_a')
    check('乙（姓名唯一）绑上了', rows[PREFIX + 'b'].dingtalk_userid, 'dt_b')
    check('丙（重名）没绑', rows[PREFIX + 'c1'].dingtalk_userid, None)
    check('丙（重名）没绑', rows[PREFIX + 'c2'].dingtalk_userid, None)
    check('丁（手机号撞两个）没绑', rows[PREFIX + 'd1'].dingtalk_userid, None)
    check('丁（手机号撞两个）没绑', rows[PREFIX + 'd2'].dingtalk_userid, None)
    check('己（原本就绑了）**没被改绑**', rows[PREFIX + 'f'].dingtalk_userid, 'dt_old')

    async with SessionLocal() as s:
        after_total = (
            await s.execute(select(func.count()).select_from(User))
        ).scalar_one()
    check('用户总数没变（**没有自动建账号**）', after_total, baseline_total)

    print()
    print('=== 4. 登录闸关着时：一次请求都不发 ===')
    app_settings.dingtalk_login_off = True
    calls_before = len(fake.calls)
    with patch.object(dt_service, 'get_client', return_value=fake):
        async with SessionLocal() as s:
            blocked = await dt_service.sync_users(s)
    check('如实回报 disabled', blocked['disabled'], True)
    check('没有发起任何外部调用', len(fake.calls), calls_before)
    check('返回的人数都是 0（不假装成功）', blocked['total'], 0)

    app_settings.dingtalk_login_off = original_login_off
    await cleanup()

    print()
    if FAILURES:
        print(f'✗ {len(FAILURES)} 项未通过：')
        for name in FAILURES:
            print(f'   - {name}')
        sys.exit(1)
    print('✓ 钉钉人员绑定回归全部通过')


if __name__ == '__main__':
    asyncio.run(main())
