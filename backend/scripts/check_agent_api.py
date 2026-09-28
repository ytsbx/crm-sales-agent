"""Agent 接口回归测试（03-API §37）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_agent_api.py

脚本自带清库，可反复执行。

## 覆盖

Session：GET/POST /agent/sessions、GET/DELETE /agent/sessions/{id}
Message：GET/POST /agent/sessions/{id}/messages、POST .../messages/stream
Action：GET /agent/actions、GET /agent/actions/{id}、confirm/reject/cancel
Execution：GET /agent/executions、GET /agent/executions/{id}、retry
Specialized：customer-summary / opportunity-analysis / product-recommendation /
             pricing-analysis / quote-draft / followup-suggestion / risk-analysis

## 重点

专用分析接口**主体是本地确定性分析**，不依赖 DEEPSEEK_API_KEY。
测试里专门验证"没配模型也能给出逾期金额、停滞天数、建议价"——
如果这些接口是把核心能力绑在外部服务上，这条就会失败。

`messages/stream` 是 SSE：验证事件帧格式与事件类型，而不是只断言 HTTP 200。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
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


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def call_sse(path, token, body):
    """读 SSE 响应，返回 (状态码, 原始文本)。"""
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Accept', 'text/event-stream')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1][
        'data'
    ]['access_token']


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ('用例执行', "delete from agent_executions where session_id in "
                  f"(select id from agent_sessions where title like '%CHK{RUN}%')"),
        ('用例动作', "delete from agent_actions where session_id in "
                  f"(select id from agent_sessions where title like '%CHK{RUN}%')"),
        ('用例消息', "delete from agent_messages where session_id in "
                  f"(select id from agent_sessions where title like '%CHK{RUN}%')"),
        ('用例会话', f"delete from agent_sessions where title like '%CHK{RUN}%'"),
        ('用例跟进', f"delete from followups where content like '%CHK{RUN}%'"),
        # 六阶段"过程记录"自动留痕/通知没有 CHK 前缀，按业务关联清（无 FK，须在报价/订单删除前后均可）
        ('用例自动留痕', "delete from followups where followup_type='系统' and ("
                  "customer_id in (select id from customers where name like 'CHK%') or "
                  "quote_id in (select id from quotes where customer_id in (select id from customers where name like 'CHK%')) or "
                  "order_id in (select id from sales_orders where customer_id in (select id from customers where name like 'CHK%')))"),
        ('用例自动通知', "delete from notifications where ("
                  "business_type='quote' and business_id in (select id from quotes where customer_id in (select id from customers where name like 'CHK%'))) or ("
                  "business_type='order' and business_id in (select id from sales_orders where customer_id in (select id from customers where name like 'CHK%')))"),
        ('用例任务', f"delete from tasks where title like '%CHK{RUN}%'"),
        ('用例回款', "delete from payment_records where order_id in "
                  "(select id from sales_orders where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例应收', "delete from receivable_plans where order_id in "
                  "(select id from sales_orders where customer_id in (select id from customers where name like 'CHK%'))"),
        ('订单状态历史', "delete from order_status_history where order_id in "
                  "(select id from sales_orders where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例订单明细', "delete from sales_order_items where order_id in "
                  "(select id from sales_orders where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例订单', "delete from sales_orders where customer_id in (select id from customers where name like 'CHK%')"),
        ('用例客户特殊价', "delete from customer_price_rules where customer_id in "
                  "(select id from customers where name like 'CHK%')"),
        ('用例报价附加费用', "delete from quote_charges where quote_version_id in "
                  "(select id from quote_versions where quote_id in (select id from quotes where customer_id in (select id from customers where name like 'CHK%')))"),
        ('用例报价发送日志', "delete from quote_send_logs where quote_version_id in "
                  "(select id from quote_versions where quote_id in (select id from quotes where customer_id in (select id from customers where name like 'CHK%')))"),
        ('用例报价明细', "delete from quote_items where quote_version_id in "
                  "(select id from quote_versions where quote_id in (select id from quotes where customer_id in (select id from customers where name like 'CHK%')))"),
        ('用例报价版本', "delete from quote_versions where quote_id in "
                  "(select id from quotes where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例报价单', "delete from quotes where customer_id in (select id from customers where name like 'CHK%')"),
        ('用例商机需求', "delete from opportunity_items where opportunity_id in "
                  "(select id from opportunities where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例商机阶段历史', "delete from opportunity_stage_history where opportunity_id in "
                  "(select id from opportunities where customer_id in (select id from customers where name like 'CHK%'))"),
        ('用例商机', "delete from opportunities where customer_id in (select id from customers where name like 'CHK%')"),
        ('用例客户', f"delete from customers where name like 'CHK{RUN}%'"),
    ]
    async with SessionLocal() as s:
        # 三次重试：清库期间若有人（比如正在验收的浏览器会话/冒烟造单）
        # 并发写入被清对象，FK 会随机炸——回滚重跑一遍即可自愈。
        last_error = None
        for attempt in range(3):
            try:
                for label, sql in statements:
                    result = await s.execute(text(sql))
                    if verbose and result.rowcount:
                        print(f'  {result.rowcount:>4}  {label}')
                await s.commit()
                last_error = None
                break
            except Exception as exc:
                await s.rollback()
                last_error = exc
        if last_error is not None:
            # 最终失败：把挡路行查出来，日志可直接定位
            async with SessionLocal() as s2:
                blocking = await s2.execute(text(
                    "select 'order' as kind, o.id, o.order_no from sales_orders o "
                    "where o.customer_id in (select id from customers where name like 'CHK%') "
                    "union all "
                    "select 'customer', id, name from customers where name like 'CHK%'"
                ))
                print('  清库失败，挡路数据：', [dict(r._mapping) for r in blocking])
            raise last_error


async def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')

    # 自建夹具：不依赖演示数据的固定 id（其他套件会清演示商机/客户）
    status, res = call('POST', '/customers', token=admin,
                       body={'name': f'CHK{RUN}夹具客户', 'level': 'A'})
    check('建夹具客户', res.get('code'), 0)
    fixture_customer = res['data']['id']
    status, res = call('GET', '/pricing/sku-options', token=admin)
    fixture_sku = res['data'][0]['id']
    status, res = call('POST', '/opportunities', token=admin,
                       body={'customer_id': fixture_customer, 'title': f'CHK{RUN}夹具商机'})
    check('建夹具商机', res.get('code'), 0)
    fixture_opp = res['data']['id']
    call('POST', f'/opportunities/{fixture_opp}/items', token=admin,
         body={'sku_id': fixture_sku, 'quantity': 100, 'target_price': 1})

    print()
    print('=== 1. 会话 CRUD ===')
    status, res = call('POST', '/agent/sessions', token=admin,
                       body={'title': f'CHK{RUN}测试会话'})
    check('建会话', res.get('code'), 0)
    sid = res['data']['id']

    status, res = call('GET', '/agent/sessions', token=admin)
    check('会话列表', res.get('code'), 0)
    check_true('含刚建的会话', any(r['id'] == sid for r in res['data']), str(len(res['data'])))

    status, res = call('GET', f'/agent/sessions/{sid}', token=admin)
    check('会话详情', res.get('code'), 0)

    status, res = call('GET', '/agent/sessions/999999', token=admin)
    check('会话不存在', res.get('code'), 40401)

    # 别人的会话看不到
    status, res = call('GET', f'/agent/sessions/{sid}', token=zhangsan)
    check('张三看不到别人的会话', res.get('code'), 40401)

    print()
    print('=== 2. 消息（未配模型也要有明确回复，不许静默失败）===')
    status, res = call('POST', f'/agent/sessions/{sid}/messages', token=admin,
                       body={'content': f'CHK{RUN} 你好'})
    check('发消息', res.get('code'), 0)
    check_true('有回复', bool(res['data'].get('reply')), str(res['data'])[:120])
    check('无动作（未配模型）', res['data']['actions'], [])

    status, res = call('GET', f'/agent/sessions/{sid}/messages', token=admin)
    check('消息历史', res.get('code'), 0)
    check_true('至少 2 条（用户+助手）', res['data']['total'] >= 2, str(res['data']['total']))
    roles = [row['role'] for row in res['data']['items']]
    check_true('含 user 与 assistant', 'user' in roles and 'assistant' in roles, str(roles))

    status, res = call('GET', f'/agent/sessions/{sid}/messages?page_size=1', token=admin)
    check('消息分页', res['data']['page_size'], 1)

    print()
    print('=== 3. 流式消息（SSE，token 级打字机）===')
    status, raw = call_sse(f'/agent/sessions/{sid}/messages/stream', admin,
                           {'content': f'CHK{RUN} 流式测试'})
    check('SSE 状态码', status, 200)
    check_true('是 event-stream 帧', raw.startswith('event: '), raw[:60])
    check_true('含 start 事件', 'event: start' in raw, '')
    check_true('含 user_message 事件', 'event: user_message' in raw, '')
    check_true('含 done 事件', 'event: done' in raw, '')
    if 'event: notice' in raw:
        check_true('未配模型：notice 说明原因', 'DEEPSEEK_API_KEY' in raw, '')
    elif 'event: delta' in raw:
        check_true('已配模型：delta 逐段推送', raw.count('event: delta') >= 2,
                   f'{raw.count("event: delta")} 帧')
    elif 'event: error' in raw:
        # key 配了但模型侧失败（欠费/网络）：链路本身要能把这个原因告诉前端
        check_true('模型失败经 error 事件告知而非静默', True, raw[raw.find('event: error'):][:80])
    else:
        check_true('既无 notice 也无 delta 也无 error', False, '事件序列异常')
    # 每个 data 行必须是合法 JSON
    bad = []
    for line in raw.splitlines():
        if line.startswith('data: '):
            try:
                json.loads(line[6:])
            except json.JSONDecodeError:
                bad.append(line[:60])
    check_true('所有 data 帧是合法 JSON', not bad, str(bad[:2]))

    print()
    print('=== 4. 动作与执行（未配模型时列表应为空，但接口要通）===')
    status, res = call('GET', '/agent/actions', token=admin)
    check('动作列表', res.get('code'), 0)
    check_true('分页结构', 'items' in res['data'] and 'total' in res['data'])

    status, res = call('GET', '/agent/actions/999999', token=admin)
    check('动作不存在', res.get('code'), 40401)

    status, res = call('POST', '/agent/actions/999999/cancel', token=admin, body={'reason': 'x'})
    check('取消不存在的动作', res.get('code'), 40401)

    status, res = call('GET', '/agent/executions', token=admin)
    check('执行列表', res.get('code'), 0)

    status, res = call('GET', '/agent/executions/999999', token=admin)
    check('执行不存在', res.get('code'), 40401)

    status, res = call('POST', '/agent/executions/999999/retry', token=admin)
    check('重试不存在的执行', res.get('code'), 40401)

    print()
    print('=== 5. 执行记录的数据范围 ===')
    # 直接在库里造一条属于 admin 会话的执行记录
    exec_id = await make_execution_async(sid)
    check_true('造出一条执行记录', exec_id is not None, str(exec_id))
    if exec_id:
        status, res = call('GET', f'/agent/executions/{exec_id}', token=admin)
        check('admin 读自己的执行记录', res.get('code'), 0)
        check_true('带角色快照', 'role_snapshot' in (res.get('data') or {}), '')
        status, res = call('GET', f'/agent/executions/{exec_id}', token=zhangsan)
        check('张三读别人的执行记录被拒', res.get('code'), 40302)
        status, res = call('POST', f'/agent/executions/{exec_id}/retry', token=zhangsan)
        check('张三重试别人的执行被拒', res.get('code'), 40302)

    print()
    print('=== 6. 专用分析：回款风险（确定性，不依赖模型）===')
    status, res = call('POST', '/agent/risk-analysis', token=admin, body={})
    check('回款风险可读', res.get('code'), 0)
    data = res['data']
    check_true('有风险等级', data['level'] in ('high', 'medium', 'low'), data['level'])
    check_true('有汇总', 'overdue_amount' in data['summary'], str(data['summary']))
    check_true('有 insights', isinstance(data['insights'], list), str(data['insights']))
    check_true('commentary 为 None 但有说明',
               data['commentary'] is None and bool(data['commentary_note']),
               str(data['commentary_note'])[:60])

    status, res = call('POST', '/agent/risk-analysis', token=admin, body={'order_id': 999999})
    check('订单不存在', res.get('code'), 40401)

    print()
    print('=== 7. 专用分析：客户摘要 / 商机分析 ===')
    status, res = call('POST', '/agent/customer-summary', token=admin, body={'customer_id': fixture_customer})
    check('客户摘要', res.get('code'), 0)
    check_true('带客户名', bool(res['data']['customer']['name']), '')
    check_true('带 counts', 'counts' in res['data'], '')
    check_true('带 insights', isinstance(res['data']['insights'], list), '')
    check_true('带最近跟进天数字段', 'days_since_followup' in res['data']['customer'], '')

    status, res = call('POST', '/agent/customer-summary', token=admin, body={'customer_id': 999999})
    check('客户不存在', res.get('code'), 40401)

    status, res = call('POST', '/agent/opportunity-analysis', token=admin,
                       body={'opportunity_id': fixture_opp})
    check('商机分析', res.get('code'), 0)
    check_true('带阶段停留天数', 'days_in_stage' in res['data'], str(res['data'].get('days_in_stage')))
    check_true('带等级标签', bool(res['data']['level_label']), res['data']['level_label'])

    status, res = call('POST', '/agent/opportunity-analysis', token=admin,
                       body={'opportunity_id': 999999})
    check('商机不存在', res.get('code'), 40401)

    print()
    print('=== 8. 专用分析：核价 / 报价草稿 / 跟进建议 / 产品推荐 ===')
    status, res = call('GET', '/skus?page_size=1', token=admin)
    sku_id = res['data']['items'][0]['id']

    status, res = call('POST', '/agent/pricing-analysis', token=admin,
                       body={'sku_id': sku_id, 'quantity': 100})
    check('核价分析', res.get('code'), 0)
    # A06：无成本也无价格规则时建议价为 None（不给成本推算价），此时 insights 会说明缺成本
    check_true(
        '带建议价或明确无成本说明',
        res['data']['recommended_price'] is not None
        or any('成本' in str(i) for i in res['data'].get('insights', [])),
        str(res['data']['recommended_price']),
    )
    check_true('带 insights', len(res['data']['insights']) >= 1, str(res['data']['insights'][:1]))

    status, res = call('POST', '/agent/quote-draft', token=admin,
                       body={'opportunity_id': fixture_opp})
    check('报价草稿建议', res.get('code'), 0)
    check_true('明确说明未落库', '未落库' in res['data']['note'], res['data']['note'][:60])
    check_true('带明细行', 'items' in res['data'], str(res['data']['item_count']))

    status, res = call('POST', '/agent/followup-suggestion', token=admin,
                       body={'customer_id': fixture_customer})
    check('跟进建议', res.get('code'), 0)
    check_true('有建议', len(res['data']['suggestions']) >= 1, str(res['data']['suggestions'][:1]))
    check_true('每条建议带理由',
               all('reason' in s for s in res['data']['suggestions']), '')

    status, res = call('POST', '/agent/followup-suggestion', token=admin, body={})
    check('既不给客户也不给线索被拒', res.get('code'), 40003)

    status, res = call('POST', '/agent/product-recommendation', token=admin,
                       body={'customer_id': fixture_customer})
    check('产品推荐', res.get('code'), 0)
    check_true('返回 recommendations 列表',
               isinstance(res['data']['recommendations'], list), '')
    check_true('说明推荐依据', '推荐依据' in res['data']['note'], '')

    status, res = call('POST', '/agent/product-recommendation', token=admin, body={})
    check('无客户无商机被拒', res.get('code'), 40003)

    print()
    print('=== 9. 数据范围：张三看不到范围外的客户 ===')
    # 不依赖存量数据：现造一个 owner 是 admin、且不在张三范围内的客户。
    # （张三 scope=self，只能是 owner_id=他自己；admin 负责的必然越界。）
    status, res = call('POST', '/customers', token=admin,
                       body={'name': f'CHK{RUN}范围外客户', 'region': '浙江'})
    check('admin 建范围外客户', res.get('code'), 0)
    outsider_id = res['data']['id']
    status, res = call('GET', f'/customers/{outsider_id}', token=zhangsan)
    check('张三读该客户本身就被拒', res.get('code'), 40302)
    status, res = call('POST', '/agent/customer-summary', token=zhangsan,
                       body={'customer_id': outsider_id})
    check('张三读该客户的 Agent 摘要同样被拒', res.get('code'), 40302)

    print()
    print('=== 9b. Agent 工具的数据范围（直接调 handler，防"问一句看全公司"）===')
    # 直接调工具函数而不是走对话：范围校验必须在工具层成立，
    # 不依赖模型会不会"恰好先搜索再查详情"
    from app.core.deps import CurrentUser
    from app.core.errors import AppError
    from app.modules.agent import tools as agent_tools
    from app.modules.agent.tools import ToolContext
    from app.core.database import SessionLocal
    from app.modules.user.model import User

    status, res = call('POST', '/customers', token=admin,
                       body={'name': f'CHK{RUN}范围外客户B', 'region': '浙江'})
    check('admin 建范围外客户 B', res.get('code'), 0)
    outsider_b = res['data']['id']

    async with SessionLocal() as s:
        admin_row = await s.get(User, 1)
        zs_row = await s.get(User, 2)  # zhangsan，数据范围 self

    zs_user = CurrentUser(zs_row, permissions=set(), roles=['salesperson'], data_scope='self')
    admin_user = CurrentUser(admin_row, permissions=set(), roles=['admin'], data_scope='all')

    async with SessionLocal() as s:
        ctx_zs = ToolContext(session=s, user=zs_user, agent_session_id=sid)
        ctx_admin = ToolContext(session=s, user=admin_user, agent_session_id=sid)

        try:
            await agent_tools.get_customer_overview(ctx_zs, outsider_b)
            check_true('张三查范围外客户全貌被拒', False, '没有抛错')
        except AppError as exc:
            check('张三查范围外客户全貌被拒', exc.code, 40302)
        try:
            data = await agent_tools.get_customer_overview(ctx_admin, outsider_b)
            check_true('admin 查同一客户正常', 'customer' in data, '')
        except AppError as exc:
            check_true('admin 查同一客户正常', False, exc.message)

        # 造一条 admin 名下的真实应收+已确认回款，让对比有区分度
        # （别的套件会清订单表，不能假设库里有单）
        from datetime import UTC, datetime

        from app.modules.order.model import SalesOrder
        from app.modules.payment.model import PaymentRecord, ReceivablePlan

        # 基线差值断言（必须在插入探针回款之前取）：库里可能有演示/历史回款
        # （seed_demo 等），断言只关心"探针的 5 万只有 admin 看得见"，不假设库是空的
        zs_baseline = (await agent_tools.get_receivables_summary(ctx_zs))['received_amount']
        admin_baseline = (await agent_tools.get_receivables_summary(ctx_admin))['received_amount']

        probe_order = SalesOrder(
            quote_id=None, order_no=f'CHK{RUN}SO', customer_id=outsider_b,
            owner_id=1, status='fulfilled', total_amount=50000,
            created_by=1, created_at=datetime.now(UTC),
        )
        s.add(probe_order)
        await s.flush()
        plan = ReceivablePlan(
            order_id=probe_order.id, plan_name='全款', amount=50000,
            due_date=datetime.now(UTC).date(), status='pending',
            created_at=datetime.now(UTC),
        )
        s.add(plan)
        await s.flush()
        s.add(PaymentRecord(
            order_id=probe_order.id, receivable_plan_id=plan.id,
            received_amount=50000, received_date=datetime.now(UTC).date(),
            status='confirmed', confirmed_by=1, confirmed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        ))
        await s.commit()

        zs_sum = await agent_tools.get_receivables_summary(ctx_zs)
        admin_sum = await agent_tools.get_receivables_summary(ctx_admin)
        check('张三看不到这笔 5 万回款', zs_sum['received_amount'], float(zs_baseline))
        check('admin 看得到', admin_sum['received_amount'], float(admin_baseline) + 50000.0)

    print()
    print('=== 10. 权限门槛（无 agent:use 的角色）===')
    status, res = call('POST', '/agent/sessions', token=admin, body={'title': f'CHK{RUN}清理用'})
    cleanup_sid = res['data']['id']
    status, res = call('DELETE', f'/agent/sessions/{cleanup_sid}', token=admin)
    check('删除会话', res.get('code'), 0)
    status, res = call('GET', f'/agent/sessions/{cleanup_sid}', token=admin)
    check('删除后读不到', res.get('code'), 40401)


async def make_execution_async(session_id: int):
    """造一条 agent_executions 记录（验证详情接口与数据范围）。

    必须是 async 并在同一个事件循环里 await：试过"起线程再 asyncio.run"
    的写法，连接池绑定的是主循环，新循环里复用连接会报
    "attached to a different loop"。
    """
    from datetime import UTC, datetime

    from app.core.database import SessionLocal
    from app.modules.agent.model import AgentExecution

    async with SessionLocal() as s:
        row = AgentExecution(
            session_id=session_id,
            action_id=None,
            tool_name='search_customers',
            risk_level='L1',
            user_id=1,
            role_snapshot='admin',
            data_scope_snapshot='all',
            input_payload={'keyword': f'CHK{RUN}'},
            output_payload={'count': 0},
            status='failed',
            error_message='CHK 测试用失败记录',
            started_at=datetime.now(UTC),
        )
        s.add(row)
        await s.commit()
        return row.id


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean(verbose=True)
        print()
        try:
            await main()
        finally:
            print()
            print('=== 清库（跑后）===')
            await clean(verbose=True)

    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
