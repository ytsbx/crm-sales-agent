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
import os
import json
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES = []
BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
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
            # 跟进 / 商机 / 线索夹具（本用例自建，见 main 的 setup）
            f"delete from followups where customer_id in {cust}",
            f"delete from opportunity_stage_history where opportunity_id in "
            f"(select id from opportunities where customer_id in {cust})",
            f"delete from opportunity_items where opportunity_id in "
            f"(select id from opportunities where customer_id in {cust})",
            f"delete from business_events where business_id in "
            f"(select id from opportunities where customer_id in {cust})",
            # 报价链（本用例给越权者造的夹具）：必须在删商机/客户之前清，
            # 否则 quotes.opportunity_id、quotes.customer_id 的外键会挡住删除。
            # quotes.current_version_id 与 quote_versions 互为引用，先置空再删版本。
            f"update quotes set current_version_id = null where customer_id in {cust}",
            f"delete from quote_charges where quote_version_id in "
            f"(select id from quote_versions where quote_id in "
            f"(select id from quotes where customer_id in {cust}))",
            f"delete from quote_items where quote_version_id in "
            f"(select id from quote_versions where quote_id in "
            f"(select id from quotes where customer_id in {cust}))",
            f"delete from quote_versions where quote_id in "
            f"(select id from quotes where customer_id in {cust})",
            f"delete from quotes where customer_id in {cust}",
            f"delete from opportunities where customer_id in {cust}",
            "delete from leads where name like :p",
            f"delete from integration_logs where business_id in {order}",
            f"delete from audit_logs where (business_type = 'order' and business_id in {order}) "
            f"or (business_type = 'customer' and business_id in {cust}) "
            f"or (business_type = 'quote' and business_id in "
            f"(select id from quotes where customer_id in {cust}))",
            f"delete from notifications where business_type = 'order' and business_id in {order}",
            f"delete from business_events where business_type = 'order' and business_id in {order}",
            f"delete from order_status_history where order_id in {order}",
            f"delete from sales_orders where customer_id in {cust}",
            f"delete from business_files where business_type = 'customer' and business_id in {cust}",
            # 合并日志先删：它引用两个客户，留着会让下面的客户删除撞外键
            f"delete from customer_merge_logs where target_customer_id in {cust} "
            f"or source_customer_id in {cust}",
            "delete from contacts where name like :p",
            f"delete from audit_logs where business_type='customer_duplicate_case' "
            f"and business_id in (select id from customer_duplicate_cases "
            f"where customer_id in {cust} or candidate_id in {cust})",
            f"delete from customer_duplicate_cases where customer_id in {cust} "
            f"or candidate_id in {cust}",
            f"delete from audit_logs where action='open_customer_duplicate_cases' "
            f"and business_type='customer' and business_id in {cust}",
            # logistics_quotes 没有 remark/owner 之类可标记的列（上次拿 remark 当标记，
            # 清理语句直接报 UndefinedColumn、整段 cleanup 中止，残留被守门套件抓到），
            # 只能按"挂在测试客户上"清。
            f"delete from logistics_quotes where customer_id in {cust}",
            # 产品附件挂载夹具（越权下载通道回归，见 main 的 3.0）：
            # 必须先删关联与审计再删文件本体，顺序反了会被 business_files 的外键挡住。
            "delete from business_files where file_id in "
            "(select id from files where file_name like :p)",
            "delete from audit_logs where business_type = 'product' and action = 'attach' "
            "and after_data->>'file_name' like :p",
            "delete from files where file_name like :p",
            # 客户标签夹具（3.2 的批量打标签回归）：customer_tags 对 customers 与 tags
            # 都有外键，必须先清关联再删客户和标签，顺序反了清理会被 FK 挡住。
            "delete from customer_tags where customer_id in "
            "(select id from customers where name like :p)",
            "delete from customer_tags where tag_id in (select id from tags where name like :p)",
            "delete from tags where name like :p",
            # 归属变更历史：转移（含越权转移）会写这张表，它引用 customers，
            # 不先删就会让下面的删客户撞外键——套件此前没有转移夹具所以一直没暴露。
            "delete from customer_owner_history where customer_id in "
            "(select id from customers where name like :p)",
            "delete from customers where name like :p",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in (select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from users where username like :u",
        ):
            await s.execute(text(sql), {
                'p': f'{PREFIX}%', 'u': f'{PREFIX.lower()}%', 'r': f'{PREFIX}%'
            })
        await s.commit()


async def main() -> int:
    from datetime import UTC, datetime

    from app.modules.customer.model import Customer
    from app.modules.followup.model import FollowUp
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.integration.model import IntegrationLog
    from app.modules.order.model import SalesOrder
    from app.modules.user.model import Role, User, role_permissions, user_roles

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

        # 临时给两名 self 范围销售删除和分配权限，确保断言测的是数据范围，
        # 而不是因为缺少模块操作权限而提前被拒。
        delete_probe_role = Role(
            code=f'{PREFIX}DELETE{stamp}', name=f'{PREFIX}删除权限探针', data_scope='self'
        )
        s.add(delete_probe_role)
        await s.flush()
        for code in ('customer:delete', 'customer:assign'):
            permission_id = (await s.execute(
                text("select id from permissions where code=:code"), {'code': code}
            )).scalar_one()
            await s.execute(role_permissions.insert().values(
                role_id=delete_probe_role.id, permission_id=permission_id
            ))
        await s.execute(user_roles.insert().values(
            user_id=owner.id, role_id=delete_probe_role.id
        ))
        await s.execute(user_roles.insert().values(
            user_id=outsider.id, role_id=delete_probe_role.id
        ))

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
        cancel_control_order = SalesOrder(
            order_no=f'{PREFIX}CANCEL{stamp}', customer_id=customer.id,
            owner_id=owner.id, sales_owner_id=owner.id,
            total_amount=Decimal('50'), currency='CNY', status='pending', created_by=owner.id,
        )
        s.add_all([inquiry, order, cancel_control_order])
        await s.flush()
        # 合并夹具：再造一个同属张三的客户，两边各挂一个**主**联系人。
        # 要验的正是"合并后目标还剩几个主联系人"——原实现把目标客户原有的主联系人
        # 也一起降级了，合并完常常一个主都不剩，得人工再设。
        from app.modules.customer.model import Contact

        source_customer = Customer(name=f'{PREFIX}来源客户-{stamp}', level='B',
                                   status='active', pool_status='private', owner_id=owner.id)
        delete_control_customer = Customer(
            name=f'{PREFIX}本人删除对照客户-{stamp}', level='C', status='active',
            pool_status='private', owner_id=owner.id,
        )
        s.add_all([source_customer, delete_control_customer])
        await s.flush()
        # 公海客户（无负责人）：用来守"别把公海误关"——刚把无归属默认改成拒绝，
        # 客户/线索必须显式放行，否则公海就看不成了
        public_customer = Customer(name=f'{PREFIX}公海客户-{stamp}', level='C',
                                   status='active', pool_status='public', owner_id=None)
        s.add(public_customer)
        await s.flush()
        s.add_all([
            Contact(customer_id=customer.id, name=f'{PREFIX}目标主联系人-{stamp}',
                    mobile='13900000002', is_primary=True),
            Contact(customer_id=source_customer.id, name=f'{PREFIX}来源主联系人-{stamp}',
                    mobile='13900000003', is_primary=True),
        ])
        await s.flush()
        # 运费试算单夹具：挂在张三客户上（其余列都有默认值）。
        # 试算单带着报价金额与地址，此前只守 product:view、谁按 id 都能读。
        from app.modules.pricing.model import LogisticsQuote
        # LogisticsQuote 有指向 skus 的外键：不先把这个模型导进来，SQLAlchemy
        # 解析不了关联，建对象时直接 NoReferencedTableError
        from app.modules.product.model import Sku

        _ = Sku  # 只为触发模型注册，解决上面的外键解析问题

        logistics_quote = LogisticsQuote(customer_id=customer.id)
        s.add(logistics_quote)
        await s.flush()

        # 跟进 / 商机 / 线索夹具：**本用例自建**，不再从演示库里"碰运气"找一条。
        # 以前这三组断言包在 `if probe/opp_id/lead_id:` 里，库里缺夹具就静默跳过、
        # 套件仍然全绿——等于没测。自建后对照用例一定存在，缺了就是硬失败。
        from app.modules.lead.model import Lead
        from app.modules.opportunity.model import Opportunity, OpportunityStage

        stage = (
            await s.execute(select(OpportunityStage).order_by(OpportunityStage.id.asc()).limit(1))
        ).scalar_one_or_none()
        followup = FollowUp(
            customer_id=customer.id,
            owner_id=owner.id,
            followup_type="电话",
            content=f"{PREFIX}越权夹具跟进",
        )
        opportunity = Opportunity(
            customer_id=customer.id,
            title=f"{PREFIX}越权夹具商机",
            stage_id=stage.id,
            owner_id=owner.id,
            status="open",
            created_by=owner.id,
        )
        lead = Lead(
            name=f"{PREFIX}越权夹具线索",
            owner_id=owner.id,
            status="assigned",
            created_by=owner.id,
        )
        s.add_all([followup, opportunity, lead])
        await s.flush()

        s.add(IntegrationLog(integration_type='erp', provider='聚水潭', direction='outbound',
                             business_type='order', business_id=order.id, status='success',
                             created_at=datetime.now(UTC)))
        await s.commit()
        cid, iid, oid = customer.id, inquiry.id, order.id
        src_cid, outsider_name = source_customer.id, outsider.username
        delete_control_cid = delete_control_customer.id
        cancel_control_oid = cancel_control_order.id
        public_cid = public_customer.id
        lq_id = logistics_quote.id
        followup_id, opp_fixture_id, lead_fixture_id = followup.id, opportunity.id, lead.id
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
    check_denied('他人取消别人的订单', call('POST', f'/orders/{oid}/cancel', outsider_token)[0])
    status, res = call('GET', f'/orders/{oid}', owner_token)
    check('他人取消尝试后本人订单仍为待处理', (res.get('data') or {}).get('status'), 'pending')
    status, res = call('POST', f'/orders/{cancel_control_oid}/cancel', owner_token)
    check('本人取消自己的订单（对照）', res.get('code'), 0)
    status, res = call('GET', f'/orders/{cancel_control_oid}', owner_token)
    check('本人取消后状态已变更', (res.get('data') or {}).get('status'), 'cancelled')

    # ---- 无归属的订单：**连"自己人"也该拒**（开关的安全侧）----
    # 把负责人清空，做出历史脏数据的形态（正常 API 造不出来，只能这样造）。
    # 原规则是"owner 为空一律放行"，于是含价格的单据猜到编号就能看；
    # 现在默认拒绝。这条与"公海客户仍可看"成对：那条守"别把公海误关"，
    # 这条守"别把无归属误开"——缺哪条都可能改坏一边。
    async with SessionLocal() as s:
        await s.execute(text("update sales_orders set owner_id = null where id = :o"),
                        {'o': oid})
        await s.commit()
    check_denied('无归属的订单时间线必须被拒（此前一律放行）',
                 call('GET', f'/orders/{oid}/timeline', owner_token)[0])
    async with SessionLocal() as s:  # 还原负责人，免得影响后面的断言与清理
        await s.execute(text("update sales_orders set owner_id = :u where id = :o"),
                        {'u': 2, 'o': oid})
        await s.commit()

    print('=== 3. 文件挂载（别人的客户）===')
    check_denied('他人往别人客户上挂附件',
                 call('POST', f'/business/customer/{cid}/files?file_id=1', outsider_token)[0])

    print('=== 3.0 产品附件挂载不能成为越权下载通道 ===')
    # 通用挂载入口（file/router.py）两个方向都校验：目标对象可见 + **源文件可见**。
    # 产品这个别名入口（POST /products/{id}/files）曾经只校验目标产品存在，
    # 于是成了越权通道：产品属 NO_OWNER_TYPES（对所有人可见），而 can_access_file
    # 只要有一条可见关联就放行 —— 把别人的 file_id 挂到任意产品上即可下载别人的原件。
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.product.model import Product

    async with SessionLocal() as s:
        product_id = (
            await s.execute(select(Product.id).where(Product.deleted_at.is_(None)).limit(1))
        ).scalar_one()
        victim_file = FileRecord(
            storage_provider='local',
            object_key=f'_fixture/{PREFIX}-victim.pdf',
            file_name=f'{PREFIX}-victim.pdf',
            mime_type='application/pdf',
            size=8,
            checksum='0' * 64,
            uploaded_by=owner.id,  # 等同于张三上传：别人一律不可见
        )
        s.add(victim_file)
        await s.flush()
        victim_file_id = victim_file.id
        await s.commit()

    check_denied('他人把别人的文件挂到产品上（越权下载通道）',
                 call('POST', f'/products/{product_id}/files?file_id={victim_file_id}',
                      outsider_token)[0])
    async with SessionLocal() as s:
        leaked = (
            await s.execute(
                select(BusinessFile.id).where(BusinessFile.file_id == victim_file_id)
            )
        ).scalars().all()
    # 挂上了就等于授权了：越权请求即使只回 403、关联却已落库，文件也已经漏了。
    check('越权挂载未留下任何关联', len(leaked), 0)
    check('上传者自己挂自己的文件（对照）',
          call('POST', f'/products/{product_id}/files?file_id={victim_file_id}',
               owner_token)[0], 200)

    print('=== 3.1 客户 / 报价删除的数据范围 ===')
    check_denied('有分配权限的他人仍不能发起别人的撞单检查',
                 call('POST', f'/customers/{cid}/duplicate-cases', outsider_token)[0])
    async with SessionLocal() as s:
        pending_count = (await s.execute(text(
            "select count(*) from customer_duplicate_cases "
            "where customer_id=:id or candidate_id=:id"
        ), {'id': cid})).scalar_one()
    check('越权撞单检查未创建记录', pending_count, 0)
    status, res = call('POST', f'/customers/{cid}/duplicate-cases', owner_token)
    check('有分配权限的本人可以发起撞单检查（对照）', res.get('code'), 0)
    # 一边是公海、一边是他人私海：只看得见其中一边也不能读或裁定整张案件。
    from app.modules.customer.model import CustomerDuplicateCase

    async with SessionLocal() as s:
        case_probe = CustomerDuplicateCase(
            customer_id=public_cid, candidate_id=cid, source='manual', status='pending',
            created_at=datetime.now(UTC),
        )
        s.add(case_probe)
        await s.flush()
        case_probe_id = case_probe.id
        await s.commit()
    status, res = call('GET', '/customer-duplicate-cases', outsider_token)
    check('撞单列表不暴露只看得见一边的案件',
          status == 200 and all(row['id'] != case_probe_id for row in (res.get('data') or [])), True)
    status, res = call('GET', '/customer-duplicate-cases', owner_token)
    check('本人可见完整客户对的撞单案件（对照）',
          status == 200 and any(row['id'] == case_probe_id for row in (res.get('data') or [])), True)
    check_denied('有分配权限但看不到候选客户仍不能裁定撞单',
                 call('POST', f'/customer-duplicate-cases/{case_probe_id}/resolve',
                      outsider_token, {'decision': 'keep_both'})[0])
    async with SessionLocal() as s:
        case_state = (await s.execute(text(
            'select status from customer_duplicate_cases where id=:id'
        ), {'id': case_probe_id})).scalar_one()
    check('越权裁定未改变案件状态', case_state, 'pending')
    status, res = call('POST', f'/customer-duplicate-cases/{case_probe_id}/resolve',
                       owner_token, {'decision': 'keep_both'})
    check('本人可裁定可见客户对（对照）', res.get('code'), 0)
    check_denied('有删除权限的他人仍不能删除别人的客户',
                 call('DELETE', f'/customers/{cid}', outsider_token)[0])
    check('跨人删除客户被拒后负责人仍可读',
          call('GET', f'/customers/{cid}', owner_token)[0], 200)
    status, res = call('DELETE', f'/customers/{delete_control_cid}', owner_token)
    check('本人删除自己的客户（对照）', res.get('code'), 0)

    quote_payload = {'customer_id': cid, 'opportunity_id': opp_fixture_id}
    _, target_quote = call('POST', '/quotes', owner_token, quote_payload)
    target_quote_id = (target_quote.get('data') or {}).get('quote_id')
    target_version_id = (target_quote.get('data') or {}).get('version_id')
    _, control_quote = call('POST', '/quotes', owner_token, quote_payload)
    control_quote_id = (control_quote.get('data') or {}).get('quote_id')
    if not target_quote_id or not control_quote_id:
        FAILURES.append('报价删除权限夹具创建失败')
    else:
        check_denied('他人删除别人的报价被拒',
                     call('DELETE', f'/quotes/{target_quote_id}', outsider_token)[0])
        check('报价删除被拒后负责人仍可读',
              call('GET', f'/quotes/{target_quote_id}', owner_token)[0], 200)
        _, res = call('DELETE', f'/quotes/{control_quote_id}', owner_token)
        check('本人删除自己的报价（对照）', res.get('code'), 0)

        # 审批规则沙盒把报价 context **全量**回给调用方（总额、毛利、最低明细毛利、
        # 客户等级、客户是否逾期），此前只判"版本存在"、且只要 quote:view，
        # 于是枚举 quote_version_id 就能读别人的报价与毛利。
        check_denied('他人拿别人的报价版本跑规则沙盒',
                     call('POST', '/approval-rules/sandbox', outsider_token,
                          {'quote_version_id': target_version_id})[0])
        check('本人跑自己报价版本的规则沙盒（对照）',
              call('POST', '/approval-rules/sandbox', owner_token,
                   {'quote_version_id': target_version_id})[0], 200)

    # ---- 同款形状（写路径堵了、读/删路径漏了）的漏口，逐条设门槛 ----
    # 这四条的价值：以后谁再把校验删掉，这里立刻红。上一轮它们抓到的第一个 bug
    # 就是我自己刚写进去的（_visible_followup 自我递归）——基准用例先红，
    # 后面结论才有意义。
    check('本人读自己客户时间线（对照）',
          call('GET', f'/customers/{cid}/timeline', owner_token)[0], 200)
    check_denied('他人读别人客户时间线',
                 call('GET', f'/customers/{cid}/timeline', outsider_token)[0])
    # 另一侧：公海客户（**无负责人**）必须仍可查看。
    # 刚把"无归属日志默认拒绝"改过来，客户/线索是显式放行的——
    # 这条守的就是"别在收紧安全口的时候把公海一起关掉"。
    check('公海客户（无负责人）的时间线仍可看',
          call('GET', f'/customers/{public_cid}/timeline', outsider_token)[0], 200)

    # 全局搜索：按手机号搜，别人的联系人不该出现（原先六类里只有它没过范围）
    status, res = call('GET', '/search?keyword=13900000001', outsider_token)
    hits = [c for c in (res.get('data') or {}).get('contacts', [])
            if c.get('customer_id') == cid]
    check('全局搜索搜不到别人的联系人', len(hits), 0)

    # 跟进：用本用例自建的夹具（见 setup）。自建保证对照用例一定存在——
    # 以前这三组断言依赖演示库里恰好有张三的既有记录，缺了就静默跳过仍全绿。
    check('本人读自己的跟进（对照）',
          call('GET', f'/followups/{followup_id}', owner_token)[0], 200)
    check_denied('他人删别人的跟进记录',
                 call('DELETE', f'/followups/{followup_id}', outsider_token)[0])
    # 跟进**主列表**也要撒网：此前 list_followups 只看查询参数、从不按数据范围过滤，
    # 任何有 followup:view 的人加个 page_size=200 就能读全公司跟进内容。
    status, res = call('GET', '/followups?page_size=200', owner_token)
    own_ids = {r['id'] for r in (res.get('data') or {}).get('items', [])}
    check('本人列表里能看到自己的跟进（对照）', followup_id in own_ids, True)
    status, res = call('GET', '/followups?page_size=200', outsider_token)
    leaked_ids = {r['id'] for r in (res.get('data') or {}).get('items', [])}
    check('他人列表里看不到别人名下的跟进', followup_id in leaked_ids, False)

    # 打样新建：拿别人的客户/商机 id 也应被拒（此前只判"存在"，能跨人引用）
    check_denied('他人用别人的客户建打样单',
                 call('POST', '/samples', outsider_token, {'customer_id': cid})[0])
    check_denied('他人用别人的商机建打样单',
                 call('POST', '/samples', outsider_token, {'opportunity_id': opp_fixture_id})[0])

    # 报价定制明细引别人的需求：给越权者造自己的客户→商机→报价，再挂张三的需求。
    # 此前 `_build_custom_item_snapshot` 只判需求存在，能把自己的报价挂到别人的需求上
    # （需求标题/编号会落进快照、流到对客文件）。
    status, res = call('POST', '/customers', outsider_token,
                       {'name': f'{PREFIX}越权者客户-{stamp}'})
    oc_cid = (res.get('data') or {}).get('id')
    status, res = call('POST', '/opportunities', outsider_token,
                       {'customer_id': oc_cid, 'title': f'{PREFIX}越权者商机-{stamp}'})
    oc_oid = (res.get('data') or {}).get('id')
    status, res = call('POST', '/quotes', outsider_token,
                       {'customer_id': oc_cid, 'opportunity_id': oc_oid})
    oc_version_id = (res.get('data') or {}).get('version_id')
    if oc_version_id:
        check_denied('他人把别人的定制需求挂进自己的报价明细',
                     call('POST', f'/quote-versions/{oc_version_id}/items', outsider_token,
                          {'inquiry_id': iid, 'unit_cost': 8, 'quoted_price': 11})[0])
    else:
        FAILURES.append('越权者报价夹具创建失败（拿不到 version_id）')

    # 商机 / 线索 / 运费试算单：同样用本用例自建的夹具（setup 里已建）。
    check('本人读自己商机时间线（对照）',
          call('GET', f'/opportunities/{opp_fixture_id}/timeline', owner_token)[0], 200)
    check_denied('他人读别人商机时间线',
                 call('GET', f'/opportunities/{opp_fixture_id}/timeline', outsider_token)[0])
    check_denied('他人读别人商机的跟进列表',
                 call('GET', f'/opportunities/{opp_fixture_id}/followups', outsider_token)[0])

    check('本人读自己线索时间线（对照）',
          call('GET', f'/leads/{lead_fixture_id}/timeline', owner_token)[0], 200)
    check_denied('他人读别人线索时间线',
                 call('GET', f'/leads/{lead_fixture_id}/timeline', outsider_token)[0])

    # 先验对照：本人读得到，才谈得上"他人读不到"
    check('本人读自己的运费试算单（对照）',
          call('GET', f'/logistics/quotes/{lq_id}', owner_token)[0], 200)
    check_denied('他人读别人的运费试算单',
                 call('GET', f'/logistics/quotes/{lq_id}', outsider_token)[0])

    # ---- 客户合并：目标客户原有的主联系人必须保住（我修的那处）----
    # 这个 bug 的形态是"一个主都不剩"，所以断言写成**恰好 1 个**——
    # 写成"至少 1 个"就抓不到它。
    status, res = call('POST', '/customers/merge', owner_token, {
        'source_customer_id': src_cid, 'target_customer_id': cid,
        'reason': f'{PREFIX}越权/合并夹具',
    })
    check('合并客户成功', res.get('code'), 0)
    async with SessionLocal() as s:
        from app.modules.customer.model import Contact

        rows = (
            await s.execute(
                select(Contact.id, Contact.is_primary).where(Contact.customer_id == cid)
            )
        ).all()
    primaries = [r for r in rows if r.is_primary]
    check('两个联系人都并到目标客户名下', len(rows), 2)
    check('合并后目标恰好剩一个主联系人', len(primaries), 1)

    print('=== 3.2 批量接口不能绕过数据范围 ===')
    # 批量入口最容易只判"存在"、漏掉"在不在你的范围内"——它们不走
    # get_visible_customer，而是裸 session.get。两处都要钉住：
    # 批量打标签能跨范围改/清空别人客户的标签；批量转移能改归属、把别人客户放公海。
    # outsider 已被授予 customer:update / customer:assign（见 setup 的探针角色），
    # 所以下面的 403 只可能来自数据范围，不会是因为缺模块权限。
    # 标签夹具自建：seed 不造标签（开发库里那几个标签是别处来的），
    # 直接 select 一个会在干净库里 NoResultFound。名字带 PREFIX 好清理。
    async with SessionLocal() as s:
        from app.modules.customer.model import Tag

        fixture_tag = Tag(name=f'{PREFIX}标签-{stamp}', type='custom', status='active')
        s.add(fixture_tag)
        await s.flush()
        real_tag_id = fixture_tag.id
        await s.commit()
    tag_batch = {'customer_ids': [cid], 'tag_ids': [real_tag_id], 'mode': 'replace'}
    check_denied('他人批量改别人客户的标签',
                 call('POST', '/customers/batch-tag', outsider_token, tag_batch)[0])
    # 混合批次（自己的客户 + 别人的客户）：既要整体被拒，也要自己的那个没被处理一半——
    # "部分生效"同样算越权成功了一半，只断言状态码会漏掉它。
    mixed_batch = {'customer_ids': [oc_cid, cid], 'tag_ids': [real_tag_id], 'mode': 'add'}

    async with SessionLocal() as s:
        own_tags_before = (
            await s.execute(
                text('select count(*) from customer_tags where customer_id = :c'), {'c': oc_cid}
            )
        ).scalar_one()
    check_denied('混合批次含他人客户时整体被拒',
                 call('POST', '/customers/batch-tag', outsider_token, mixed_batch)[0])
    async with SessionLocal() as s:
        own_tags_after = (
            await s.execute(
                text('select count(*) from customer_tags where customer_id = :c'), {'c': oc_cid}
            )
        ).scalar_one()
    check('被拒的混合批次没有对自己的客户生效一半', own_tags_after, own_tags_before)

    check_denied('他人批量改别人客户的负责人',
                 call('POST', '/customers/batch-transfer', outsider_token,
                      {'customer_ids': [cid], 'owner_id': None,
                       'reason': f'{PREFIX}越权'} )[0])
    async with SessionLocal() as s:
        owner_after = (
            await s.execute(select(Customer.owner_id).where(Customer.id == cid))
        ).scalar_one()
    check('被拒的批量转移未把别人客户放进公海', owner_after, owner.id)
    check('本人批量给自己客户打标签（对照）',
          call('POST', '/customers/batch-tag', owner_token, tag_batch)[0], 200)

    print('=== 3.3 Agent 入口不能绕过数据范围 ===')
    async with SessionLocal() as s:
        from app.modules.product.model import Sku

        sku_id = (await s.execute(select(Sku.id).limit(1))).scalar_one()
    # 客户专属价挂在客户上：不校验可见性时，有 agent:use 的人换个 customer_id
    # 就能读到别人客户的协议价；响应里还带着成本与授权底价（可反推成本）。
    check_denied('他人拿别人的客户跑 Agent 核价分析',
                 call('POST', '/agent/pricing-analysis', outsider_token,
                      {'sku_id': sku_id, 'quantity': 10, 'customer_id': cid})[0])
    check('本人拿自己的客户跑 Agent 核价分析（对照）',
          call('POST', '/agent/pricing-analysis', owner_token,
               {'sku_id': sku_id, 'quantity': 10, 'customer_id': cid})[0], 200)

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
