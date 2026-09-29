"""越权回归：业务员看不到 / 改不了别人的数据（审查验收标准第 1 条）。

跑法（后端要在 8000 跑着——判定发生在接口层）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_data_scope.py

## 为什么要有这条

这个项目的数据范围（`data_scope`）不是新东西，但**每加一条新路径都得重新过一遍**：
认证（有没有权限）和授权（数据在不在你范围内）是两件事，只写 `require_permission`
就会漏掉后者。2026-09-29 那轮的代码审查里，**四条越权全是这个形态**：

- 文件关联：能把别人的文件挂到自己对象上再下载
- 订单建单/转单：能拿别人的客户或报价建单
- 钉钉 OA：能对别人的定制需求发起审批、读审批历史
- 集成日志：能看到别人订单的同步报文与错误

所以这条套件不是"测一次就完"，而是**新接口的准入门槛**：
新增"按 id 直取业务对象"的接口时，照这里的形状补一条断言。

夹具：一个独立的新业务员（与张三同为销售角色、数据范围 self），
业务对象挂在 **张三** 名下，然后用新业务员去访问——预期全部被拒。
跑完即清，不碰演示数据。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES = []
BASE = 'http://127.0.0.1:8000/api/v1'
PREFIX = 'CHKSCOPE'
#: 被拒的两种正常表现：403（范围/权限拒绝）或 404（不可见时不暴露存在性）
DENIED = (403, 404)


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_denied(label, status):
    print(f'  {"OK  " if status in DENIED else "FAIL"} {label}: HTTP {status}（期望被拒 403/404）')
    if status not in DENIED:
        FAILURES.append(label)


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or '{}')


def login(username: str, password: str) -> str:
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    if res.get('code') != 0:
        raise SystemExit(f'登录失败（{username}）：后端没在 8000 跑？')
    return res['data']['access_token']


async def cleanup():
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        order = f"(select id from sales_orders where customer_id in {cust})"
        for sql in (
            "delete from oa_instances where inquiry_id in "
            "(select id from custom_inquiries where inquiry_no like :p)",
            "delete from custom_inquiries where inquiry_no like :p",
            f"delete from integration_logs where business_id in {order}",
            f"delete from order_status_history where order_id in {order}",
            f"delete from sales_orders where customer_id in {cust}",
            f"delete from business_files where business_type = 'customer' and business_id in {cust}",
            "delete from customers where name like :p",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            "delete from users where username like :u",
        ):
            await s.execute(text(sql), {'p': f'{PREFIX}%', 'u': f'{PREFIX.lower()}%'})
        await s.commit()


async def main() -> int:
    from datetime import UTC, datetime

    from app.modules.customer.model import Customer
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.integration.model import IntegrationLog
    from app.modules.order.model import SalesOrder
    from app.modules.user.model import Role, User, user_roles

    stamp = int(time.time())
    await cleanup()

    async with SessionLocal() as s:
        owner = (await s.execute(select(User).where(User.username == 'zhangsan'))).scalars().one()
        outsider = User(name=f'{PREFIX}业务员-{stamp}', username=f'{PREFIX.lower()}_{stamp}',
                        password_hash='x', status='active')
        s.add(outsider)
        await s.flush()
        # 与张三同角色（销售、self 范围），这样 403 只可能来自数据范围而不是缺权限
        sales_role = (await s.execute(select(Role).where(Role.code == 'salesperson'))).scalars().one()
        await s.execute(user_roles.insert().values(user_id=outsider.id, role_id=sales_role.id))

        customer = Customer(name=f'{PREFIX}张三客户-{stamp}', level='A', status='active',
                            pool_status='private', owner_id=owner.id)
        s.add(customer)
        await s.flush()
        inquiry = CustomInquiry(inquiry_no=f'{PREFIX}{stamp}', title='越权夹具需求', version=1,
                                quantity=Decimal('1'), status='open',
                                customer_id=customer.id, created_by=owner.id)
        order = SalesOrder(order_no=f'{PREFIX}{stamp}', customer_id=customer.id,
                           owner_id=owner.id, sales_owner_id=owner.id,
                           total_amount=Decimal('100'), currency='CNY', status='pending',
                           created_by=owner.id)
        s.add_all([inquiry, order])
        await s.flush()
        s.add(IntegrationLog(integration_type='erp', provider='聚水潭', direction='outbound',
                             business_type='order', business_id=order.id, status='success',
                             created_at=datetime.now(UTC)))
        await s.commit()
        cid, iid, oid, outsider_name = customer.id, inquiry.id, order.id, outsider.username
        # 夹具用户需要能登录：设一个临时口令（用与张三相同的哈希来源）
        from app.core.security import hash_password

        outsider.password_hash = hash_password('123456')
        await s.commit()

    owner_token = login('zhangsan', '123456')
    outsider_token = login(outsider_name, '123456')

    print('=== 1. 定制询价 / 钉钉 OA（别人的需求）===')
    check('本人查自己的审批历史（对照）', call('GET', f'/inquiries/{iid}/oa-approvals', owner_token)[0], 200)
    check_denied('他人查审批历史', call('GET', f'/inquiries/{iid}/oa-approvals', outsider_token)[0])
    check_denied('他人发起审批', call('POST', f'/inquiries/{iid}/oa-approval', outsider_token, {'resubmit': False})[0])

    print('=== 2. 订单（别人的单）===')
    check('本人读自己的订单（对照）', call('GET', f'/orders/{oid}', owner_token)[0], 200)
    check_denied('他人读订单详情', call('GET', f'/orders/{oid}', outsider_token)[0])
    check_denied('他人改订单', call('PATCH', f'/orders/{oid}', outsider_token, {'remark': '越权尝试'})[0])
    check_denied('他人生成应收', call('POST', f'/orders/{oid}/receivables/generate', outsider_token,
                                      {'ratios': [1], 'first_due_date': '2026-12-01'})[0])

    print('=== 3. 文件挂载（别人的客户）===')
    check_denied('他人往别人客户上挂附件',
                 call('POST', f'/business/customer/{cid}/files?file_id=1', outsider_token)[0])

    print('=== 4. 集成日志（别人订单的同步记录）===')
    status, res = call('GET', '/integrations/erp/sync-logs?page_size=200', outsider_token)
    rows = (res.get('data') or {}).get('items') or []
    leaked = [r for r in rows if r.get('business_id') == oid]
    check('他人看不到别人订单的日志', status, 200)
    check('泄漏条数', len(leaked), 0)

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('越权回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
