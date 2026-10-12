#!/usr/bin/env python3
"""运费死胡同的两道闸（主人 2026-10-12 拍板，方案 A+B）。

## 那个死胡同

    提交审批（**不校验运费**）→ 通过 → 版本锁死（费用入口只对草稿开放）
    → 「标记已发送」才发现缺运费 → 撤回也救不了（撤回只对「审批中」有效）
    → 唯一出路：新建版本 + 补运费 + **重走一遍审批**

主人对此定的口径（三条，逐条对应下面的用例）：

1. **方案 A**：把运费闸提前到「提交审批」—— 没确认运费就别想报批；
2. **方案 B**：已经批过、已经锁死的版本，允许**只补/改运费**（不用重审）。
   判据是「运费是代收代付、不进产品价与利润」（`item_unit_product_cost` 取
   `includes_freight=False`），而且审批分档用的 `total_amount` 在**批的那一刻
   运费还是 0** —— 事后补它不会追溯改变已定的档位，所以不需要重审；
3. 补完之后旧版仍能「标记已发送 → 客户接受 → 转订单」，这条路要真的通。

## 必测的反面（"只放行运费"不能变成"锁版后随便改"）

- 锁版后加**非运费**费用 → 必须拒（否则批过的总额能被事后改动，审批白做）；
- 锁版后把**运费改成别的分类** → 必须拒（"改分类"等于换了一笔钱）；
- 锁版后改**已存在的非运费费用**（折扣）→ 必须拒；
- 草稿态一切照旧（该能加折扣就能加）。

## 跑法

    DB=crm_iso_test_freight
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head && PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8091 &
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL API_BASE=http://127.0.0.1:8091/api/v1 CI=true \\
      .venv/bin/python scripts/check_freight_deadlock.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = 'CHKFREIGHT'
FAILED: list[str] = []
PASSED = 0


def check(label: str, got, want) -> None:
    global PASSED
    if got == want:
        PASSED += 1
        print(f'  OK   {label}: {got!r}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}: 实际 {got!r}，期望 {want!r}')


def check_true(label: str, value: bool, detail: str = '') -> None:
    global PASSED
    if value:
        PASSED += 1
        print(f'  OK   {label}{f"（{detail}）" if detail else ""}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}{f"（{detail}）" if detail else ""}')


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(
        f'{require_api_base()}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {'code': exc.code, 'message': raw[:200]}


def login(username: str, password: str) -> str:
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    return res['data']['access_token']


def new_quote(token: str, tag: str) -> tuple[int, int, int]:
    """建一份带明细的草稿，返回 (报价 id, 版本 id, 客户 id)。"""
    _, res = call('POST', '/customers', token=token, body={'name': f'{RUN}{tag}客户'})
    customer = res['data']['id']
    _, res = call('POST', '/opportunities', token=token,
                  body={'customer_id': customer, 'title': f'{RUN}{tag}商机'})
    opportunity = res['data']['id']
    _, res = call('POST', '/quotes', token=token,
                  body={'opportunity_id': opportunity, 'customer_id': customer})
    quote = res['data']
    call('POST', f"/quote-versions/{quote['version_id']}/items", token=token,
         body={'sku_id': 1, 'quantity': 10, 'quoted_price': 150})
    return quote['quote_id'], quote['version_id'], customer


def add_freight(token: str, version_id: int, amount: str) -> tuple[int, dict]:
    return call('POST', f'/quote-versions/{version_id}/charges', token=token,
                body={'charge_type': 'logistics', 'description': '实际运费', 'amount': amount})


def version_state(token: str, quote_id: int, version_id: int) -> dict:
    """取这一版的状态（**报价单 + 版本**两边的字段揉成一份，便于断言）。

    ⚠️ 两个接口的形状不一样，这一步必须做对（我在这里连错两次）：
      · `GET /quotes/{id}?version_id=`      → **扁平**：`status` / `approval_status`
      · `GET /quote-versions/{id}`          → 分块：`version` / `quote` / `charges` / `items`
    而 `sent_at` **只在后者**（在 `data.version` 里），前端详情页读的也是它。
    我第一版从前者读顶层 `sent_at`，永远拿到 `None` —— 于是"sent_at 被清掉"
    那几条断言全都在**空转**，我还顺着这个假象去改了一处没坏的代码。
    """
    _, quote_res = call('GET', f'/quotes/{quote_id}?version_id={version_id}', token=token)
    _, version_res = call('GET', f'/quote-versions/{version_id}', token=token)
    out = dict(quote_res['data'])
    out.update(version_res['data'].get('version') or {})
    return out


def freight_reason(token: str, quote_id: int, version_id: int) -> str | None:
    _, res = call('GET', f'/quote-versions/{version_id}', token=token)
    return res['data'].get('freight_unconfirmed_reason')


def _confirm_master(sku_id: int = 1) -> None:
    """确认这个 SKU 的对外三字段主数据。

    正式发送还有**另一道**与运费无关的闸（`ensure_items_master_confirmed`：
    印给客户的名称/规格/单位必须来自已确认的主数据）。本脚本聚焦运费，
    所以夹具先把这道闸过掉 —— 否则发送会被主数据拦住，
    而失败消息看着像"运费那条规则没生效"（实测就是这么误导的）。
    """
    import asyncio
    import threading

    def worker() -> None:
        async def go() -> None:
            from sqlalchemy.ext.asyncio import create_async_engine

            from app.core.config import settings
            from app.core.database import SessionLocal
            from app.modules.product.master import QUOTE_DISPLAY_FIELDS, confirm_local_master

            engine = create_async_engine(settings.database_url)
            try:
                async with SessionLocal() as session:
                    try:
                        await confirm_local_master(
                            session, sku_id=sku_id, operator_id=1,
                            fields=tuple(QUOTE_DISPLAY_FIELDS),
                        )
                        await session.commit()
                        print(f'  · [夹具] SKU {sku_id} 主数据已确认')
                    except Exception as exc:  # noqa: BLE001 —— 夹具失败要说出来
                        print(f'  · [夹具] 确认主数据失败：{type(exc).__name__}: {str(exc)[:120]}')
                        raise
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()


def _force_approved(version_id: int) -> None:
    """把版本直接置成"已通过"（造存量夹具，见 B 段的说明）。

    ⚠️ 本脚本的 `main()` 是**同步**的，所以这里用"独立线程 + 独立事件循环"，
    并且**用完 `engine.dispose()`** —— 连接池绑在创建它的那个事件循环上，
    不 dispose 的话第二次调用就报 "attached to a different loop"
    （`check_quote_center_acceptance` 里踩过同一个坑，那份注释写得很清楚）。
    """
    import asyncio
    import threading

    def worker() -> None:
        async def go() -> None:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine

            from app.core.config import settings

            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    # ⚠️ **报价单的状态也要一起置**：发送闸（`mark_version_sent`）要求
                    # `quote.status in ('approved', 'sent')`，只改版本会被拒
                    # （消息是"当前报价状态不能标记发送"，看着像另一个问题）。
                    # 正常流程里 `submit_for_approval` 两处都会置，夹具要跟它一致。
                    await conn.execute(text(
                        "update quote_versions set approval_status = 'approved' where id = :i"
                    ), {'i': version_id})
                    await conn.execute(text(
                        "update quotes set status = 'approved' where current_version_id = :i"
                    ), {'i': version_id})
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()


def _cleanup() -> None:
    """清掉本脚本造的夹具（RUN 前缀的客户 / 商机 / 报价 / 报价版本）。

    ⚠️ **顺序按外键依赖来**：明细与附加费用指向版本、版本指向报价、
    报价与商机指向客户。少一段就会撞 FK（`check_approval_rules` 里踩过）。
    本脚本是最先跑的那批之一，残留会连累后面的**守门套件**
    （`check_fixture_residue` 实测会因此报红）。
    """
    import asyncio
    import threading

    def worker() -> None:
        async def go() -> None:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine

            from app.core.config import settings

            like = f"{RUN}%"
            vids = ("(select id from quote_versions where quote_id in "
                    "(select id from quotes where customer_id in "
                    "(select id from customers where name like :p)))")
            # ⚠️ 顺序 = 外键依赖顺序，且**必须包含 `quote_send_logs`**：
            # 发送记录也指向报价版本，本脚本 B 段会真的发几次，
            # 漏了它就删不掉版本（实测 FK 拦下，清理半途而废、残留客户）。
            statements = [
                f"delete from quote_send_logs where quote_version_id in {vids}",
                f"delete from quote_charges where quote_version_id in {vids}",
                f"delete from quote_items where quote_version_id in {vids}",
                f"delete from approval_instances where business_id in {vids}",
                "delete from quote_versions where quote_id in "
                "(select id from quotes where customer_id in "
                "(select id from customers where name like :p))",
                "delete from quotes where customer_id in "
                "(select id from customers where name like :p)",
                "delete from opportunity_items where opportunity_id in "
                "(select id from opportunities where customer_id in "
                "(select id from customers where name like :p))",
                "delete from opportunity_stage_history where opportunity_id in "
                "(select id from opportunities where customer_id in "
                "(select id from customers where name like :p))",
                "delete from opportunities where customer_id in "
                "(select id from customers where name like :p)",
                "delete from customers where name like :p",
            ]
            engine = create_async_engine(settings.database_url)
            try:
                for sql in statements:
                    async with engine.begin() as conn:
                        await conn.execute(text(sql), {'p': like})
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()


def main() -> None:
    require_api_base()
    require_isolated_db()
    admin = login('admin', 'admin123')
    _cleanup()  # 先清一遍：上一次跑到一半失败会留下夹具

    # ================================================== 方案 A：提交审批前置
    print()
    print('=== A. 缺运费不许提交审批（从前这里不校验，才有的死胡同）===')
    qid_a, vid_a, _ = new_quote(admin, 'A')
    check('前置：草稿态运费未确认', freight_reason(admin, qid_a, vid_a) is not None, True)
    status, res = call('POST', f'/quote-versions/{vid_a}/submit-approval', token=admin, body={})
    check('缺运费提交审批 → 被拒', status, 422)
    check_true('理由说清了是运费',
               '运费' in str(res.get('message')), str(res.get('message'))[:60])
    after = version_state(admin, qid_a, vid_a)
    check('被拒后版本仍是草稿（没有半提交）', after.get('approval_status') or 'not_submitted',
          'not_submitted')

    print()
    print('=== A2. 确认运费后就能提交（含"明确 0 元"）===')
    status, res = add_freight(admin, vid_a, '0')
    check('填 0 元运费', res.get('code'), 0)
    check('0 元也算已确认（显式填就是确认）', freight_reason(admin, qid_a, vid_a), None)
    status, res = call('POST', f'/quote-versions/{vid_a}/submit-approval', token=admin, body={})
    check('这次提交成功', res.get('code'), 0)

    # ================================================== 方案 B：锁版后只补运费
    print()
    print('=== B. 已经批过的版本：允许只补运费（不用重审）===')
    print('  · 场景是"**方案 A 上线之前就已经批过、并且缺运费**"的那些存量单。')
    print('    方案 A 之后新单不会再走到这个状态，但它们还在库里、用户还被卡着 ——')
    print('    所以这里**直接改库**把版本置成"已通过 + 运费未确认"，')
    print('    而不是走接口（接口现在会按方案 A 拒掉，那正是刚验过的 A 段）。')
    # 先把与运费无关的那道闸（主数据确认）过掉，否则发送会被它拦住，
    # 而失败消息看着像"运费规则没生效"（实测误导过一轮）
    _confirm_master()
    qid_b, vid_b, _ = new_quote(admin, 'B')
    # 先在草稿态加一条**折扣**（非运费），批完之后它必须仍然改不动
    _, res = call('POST', f'/quote-versions/{vid_b}/charges', token=admin,
                  body={'charge_type': 'discount', 'description': '年度让价', 'amount': '50'})
    check('草稿态加折扣（为下面的反面准备）', res.get('code'), 0)
    discount_id = res['data']['id']
    _force_approved(vid_b)
    ver = version_state(admin, qid_b, vid_b)
    check('构造成功：版本已是 approved', ver['approval_status'], 'approved')
    check('此刻运费仍未确认（存量单的现场）',
          freight_reason(admin, qid_b, vid_b) is not None, True)

    # ① 补运费 —— 这条必须放行
    status, res = add_freight(admin, vid_b, '80')
    check('★批过的版本补运费 → 放行', res.get('code'), 0)
    freight_id = res['data']['id']
    check('补完运费后发送闸开了', freight_reason(admin, qid_b, vid_b), None)

    # ② 改运费金额 —— 也放行（重新确认一次）
    status, res = call('PATCH', f'/quote-charges/{freight_id}', token=admin, body={'amount': '95'})
    check('改运费金额 → 放行（并重新确认）', res.get('code'), 0)
    check('改完仍是已确认', freight_reason(admin, qid_b, vid_b), None)

    # ③ 删运费 —— 放行（补错了要能撤）
    status, res = add_freight(admin, vid_b, '10')
    extra_freight = res['data']['id']
    status, res = call('DELETE', f'/quote-charges/{extra_freight}', token=admin)
    check('删掉补错的运费 → 放行', res.get('code'), 0)

    # ---------------- 反面：只放行运费，不能变成"锁版后随便改"
    print()
    print('=== B4. 反面：锁版后**非运费**一律拒（否则审批白做）===')
    status, res = call('POST', f'/quote-versions/{vid_b}/charges', token=admin,
                       body={'charge_type': 'service', 'description': '服务费', 'amount': '20'})
    check('锁版后加服务费 → 拒', res.get('code'), 42204)
    status, res = call('POST', f'/quote-versions/{vid_b}/charges', token=admin,
                       body={'charge_type': 'discount', 'description': '再让价', 'amount': '30'})
    check('锁版后加折扣 → 拒', res.get('code'), 42204)
    status, res = call('PATCH', f'/quote-charges/{discount_id}', token=admin,
                       body={'amount': '99'})
    check('锁版后改已有折扣 → 拒', res.get('code'), 42204)
    status, res = call('DELETE', f'/quote-charges/{discount_id}', token=admin)
    check('锁版后删折扣 → 拒', res.get('code'), 42204)
    # 把运费**改成**服务费：前后分类都看，必须拒
    status, res = call('PATCH', f'/quote-charges/{freight_id}', token=admin,
                       body={'charge_type': 'service'})
    check_true('★把运费改成服务费 → 拒（判据要看"改之后"）',
               res.get('code') == 42204, f"code={res.get('code')}")

    # 被拒的改动不该留下副作用。
    #
    # ⚠️ **说清楚这条断言的成色**：它其实是"体检"，不是"守门"。
    # 我一度以为这里是真 bug（清空 `sent_at` 写在校验之前），但**撤掉那处顺序修正
    # 这条断言照样绿** —— 因为真正的保护在 `get_db` 依赖里：
    #
    #     except Exception: await session.rollback(); raise
    #
    # 抛错 → 回滚 → 对象上的改动被丢弃，所以"被拒却改了状态"在下游不可达。
    # 那处顺序仍然改对了（**校验失败不许有副作用**是原则，不该依赖调用方回滚），
    # 但它是**防御性**的，不是这次修出来的真 bug。留着这条断言作为体检；
    # 别把它当成"改了顺序就会被它抓住"。
    status, res = call('POST', f'/quote-versions/{vid_b}/mark-sent', token=admin,
                       body={'channel': '企业微信', 'receiver': '李经理'})
    check_true('（先把 B 版发出去，供下面用）', res.get('code') == 0, f"code={res.get('code')}")
    _v = version_state(admin, qid_b, vid_b)
    check_true('前置：此刻确实是已发送', _v.get('sent_at') is not None, f"sent_at={_v.get('sent_at')}")
    # 一次注定被拒的改动
    status, res = call('PATCH', f'/quote-charges/{discount_id}', token=admin, body={'amount': '77'})
    check('（这次改动被拒）', res.get('code'), 42204)
    _v2 = version_state(admin, qid_b, vid_b)
    check_true('★被拒的改动**没有**清掉 sent_at（校验失败不许有副作用）',
               _v2.get('sent_at') is not None, f"sent_at={_v2.get('sent_at')}")

    # ---------------- 补完之后整条路要真的通
    print()
    print('=== B6. 已发送之后再改运费 → 放行，但"已发送"要清掉（客户手里那份已不一致）===')
    # ⚠️ 顺序要对：**先补运费 → 才可能发送成功 → 才有"已发送之后改运费"这件事**。
    # 我第一版把改运费写在发送之前，于是"前置：改之前是 sent"那条断言空转了
    # （发送根本没成功），白白多排查一轮。
    status, res = call('POST', f'/quote-versions/{vid_b}/mark-sent', token=admin,
                       body={'channel': '企业微信', 'receiver': '李经理'})
    check_true('补完运费后发送成功', res.get('code') == 0,
               f"code={res.get('code')} msg={str(res.get('message'))[:70]}")
    status, res = call('POST', f'/quote-versions/{vid_b}/accept', token=admin)
    check_true('客户接受成功', res.get('code') == 0, f"code={res.get('code')}")
    # ⚠️ 前置断言要钉"**已经发送过**"这件事本身，而不是某个状态串：
    # 客户接受之后报价状态会从 `sent` 变成 `accepted` —— 我第一版断言 `== 'sent'`，
    # 于是在这条路径上假红（状态机往后走了一格，跟"有没有发过"是两件事）。
    # 判据用版本上的 `sent_at`（发送事实），它与前端的判断同源。
    _v_before = version_state(admin, qid_b, vid_b)
    check_true('前置：改之前这一版确实已发送（sent_at 有值）',
               _v_before.get('sent_at') is not None, f"sent_at={_v_before.get('sent_at')}")

    status, res = call('PATCH', f'/quote-charges/{freight_id}', token=admin, body={'amount': '120'})
    check('已发送的版本改运费 → 放行', res.get('code'), 0)
    _v_after = version_state(admin, qid_b, vid_b)
    check('★sent_at 被清掉（必须重新登记发送）', _v_after.get('sent_at'), None)
    _after = version_state(admin, qid_b, vid_b)
    check_true('★报价状态不再是 sent（前端据此重新显示"标记已发送"）',
               _after.get('status') != 'sent', f"status={_after.get('status')}")
    # 重新登记一次发送：改完运费之后这条路要能走通（不能把单子改成发不出去）
    status, res = call('POST', f'/quote-versions/{vid_b}/mark-sent', token=admin,
                       body={'channel': '企业微信', 'receiver': '李经理'})
    check_true('★改完运费可以重新登记发送（不是把单子改死）', res.get('code') == 0,
               f"code={res.get('code')} msg={str(res.get('message'))[:70]}")

    # ---------------- 草稿态一切照旧
    print()
    print('=== C. 草稿态不受影响（该能加折扣就加）===')
    qid_c, vid_c, _ = new_quote(admin, 'C')
    status, res = call('POST', f'/quote-versions/{vid_c}/charges', token=admin,
                       body={'charge_type': 'discount', 'description': '草稿折扣', 'amount': '30'})
    check('草稿态加折扣 → 放行', res.get('code'), 0)
    status, res = call('POST', f'/quote-versions/{vid_c}/charges', token=admin,
                       body={'charge_type': 'service', 'description': '草稿服务费', 'amount': '15'})
    check('草稿态加服务费 → 放行', res.get('code'), 0)


if __name__ == '__main__':
    try:
        main()
    finally:
        _cleanup()
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
