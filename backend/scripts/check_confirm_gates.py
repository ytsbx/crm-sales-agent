"""会「切换当前依据 / 覆盖金额 / 结束流程 / 登记商务事实 / 写外部系统」的动作，
必须先确认再执行（审查 2026-10-10 的清单）。

为什么（审查原话）：有些按钮点一下就真的执行了，而它们不是普通的"新增草稿"。
最典型的是「新建版本」—— 它还会**切换当前版本、把报价状态改回草稿、
并自动结束旧版还在走的审批流程**，而系统里**没有**撤销新版本、恢复上述状态的入口。

修法（主人 2026-10-10 拍板：**弹窗 + 服务器也拦一道**）：
后端这五个动作不带 `confirm=true` 一律返回 `42206`，**并且把"点了会怎样"
写在报错文案里**（前端拿它当确认弹窗的正文，不再自己拼一份）。

本套件守两件事：
  ① 不带 confirm → 必拒，且**确实没有执行**（不是"拒了但改了一半"）；
  ② 文案说得清后果（含"会复制哪一版""金额多少""撤回不了"这类关键信息）。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_cg \
    PYTHONPATH=. .venv/bin/python scripts/check_confirm_gates.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from _db_helper import db  # noqa: E402

passed = 0
failed: list[str] = []
MARK = "CHKCG"
CONFIRM_CODE = 42206


def check(label, got, want):
    global passed
    if str(got) == str(want):
        passed += 1
        print(f"  OK   {label}: {got!r}")
    else:
        failed.append(label)
        print(f"  FAIL {label}: {got!r}（期望 {want!r}）")


def check_api(label, res, want_code):
    """断言接口的 `code`，**失败时把整个响应打出来**。

    为什么单独一个（踩过）：CI 里 `create_version` 返回 50001（服务器内部错误），
    而 `check()` 只打印 `50001` —— 到底是哪一步炸的、什么异常，日志里一个字都没有，
    只能靠猜（白折腾了好几轮）。把 `res` 原样打出来，至少能看到 message；
    再配合后端日志 tail，就能定位。
    """
    global passed
    got = res.get("code")
    if str(got) == str(want_code):
        passed += 1
        print(f"  OK   {label}: code={got}")
    else:
        failed.append(label)
        print(f"  FAIL {label}: code={got}（期望 {want_code}）")
        print(f"       完整响应：{res!r}"[:600])


def check_true(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  OK   {label} {detail}")
    else:
        failed.append(label)
        print(f"  FAIL {label} {detail}")


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&=%"), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def must_int(label: str, sql: str) -> int:
    """取一个**必须存在**的整数 id。

    为什么单独一个函数（踩过）：`db()` 出错时返回 `'SQLERR:...'`、查不到时返回
    `''`，两者都能悄悄拼进后面的 SQL，变成 `business_id, , 'pending'` 这种
    语法错，报错信息完全指不到真正的原因。这里**当场**把问题喊出来。
    """
    raw = db(sql)
    if not str(raw).strip().isdigit():
        raise AssertionError(f"夹具 [{label}] 取不到 id：{raw!r}（SQL: {sql[:90]}）")
    return int(raw)


def _cleanup() -> None:
    """清掉本套件的夹具。

    ⚠️ 每一步都**检查返回值**：`db()` 出错时返回 `'SQLERR:...'` 而**不抛异常**，
    静默失败的表现只是"最后客户没删掉"，根本看不出是哪一步被外键挡住了
    （这就是本套件第一版留下残渣的原因）。
    """
    custs = f"(select id from customers where name like '{MARK}%')"
    orders = f"(select id from sales_orders where customer_id in {custs})"
    skus = f"(select id from skus where sku_code like '{MARK}%')"
    for sql in (
        f"delete from payment_records where order_id in {orders}",
        f"delete from receivable_plans where order_id in {orders}",
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in {orders})",
        f"delete from order_shipment_batches where order_id in {orders}",
        f"delete from order_milestones where order_id in {orders}",
        f"delete from order_status_history where order_id in {orders}",
        f"delete from sales_order_items where order_id in {orders}",
        f"delete from sales_orders where customer_id in {custs}",
        # ⚠️ 样品有三层引用，必须**由内到外**删（踩过：只删 sample_requests，
        # 被 `sample_items.sample_request_id` 的外键挡住；而 `db()` 把错误当
        # 字符串返回、不抛异常，于是这次失败是完全静默的，只表现为"客户删不掉"）：
        #   sample_items / sample_shipments → sample_requests（含自引用 parent_id）
        f"delete from sample_items where sample_request_id in (select id from sample_requests where customer_id in {custs})",
        f"delete from sample_shipments where sample_request_id in (select id from sample_requests where customer_id in {custs})",
        f"update sample_requests set parent_id = null where customer_id in {custs}",
        f"delete from sample_requests where customer_id in {custs}",
        f"delete from quote_charges where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        # 审批记录/实例要**先删**：它们引用 quote_versions，不删就删不掉版本，
        # 于是夹具残留（本套件自己插过一条待审批实例）。
        "delete from approval_records where approval_instance_id in (select id from approval_instances where business_type='quote_version' and business_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in " + custs + ")))",
        "delete from approval_instances where business_type='quote_version' and business_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in " + custs + "))",
        f"delete from quote_items where quote_version_id in (select id from quote_versions where quote_id in (select id from quotes where customer_id in {custs}))",
        f"delete from quote_versions where quote_id in (select id from quotes where customer_id in {custs})",
        f"delete from quotes where customer_id in {custs}",
        f"delete from opportunity_stage_history where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunity_items where opportunity_id in (select id from opportunities where title like '{MARK}%')",
        f"delete from opportunities where title like '{MARK}%'",
        f"delete from contacts where customer_id in {custs}",
        f"delete from customers where name like '{MARK}%'",
        f"delete from price_rules where sku_id in {skus}",
        f"delete from product_costs where sku_id in {skus}",
        f"delete from skus where sku_code like '{MARK}%'",
        f"delete from products where name like '{MARK}%'",
    ):
        out = db(sql)
        if str(out).startswith("SQLERR"):
            raise AssertionError(f"清理失败（{sql[:70]}…）：{out[:160]}")


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    _cleanup()

    print("\n=== ① 新建版本：必须确认 ===")
    call("POST", "/customers", admin, {"name": f"{MARK}-客户", "level": "C"})
    cid = int(db(f"select id from customers where name='{MARK}-客户' order by id desc limit 1"))
    call("POST", "/opportunities", admin,
         {"title": f"{MARK}-商机", "customer_id": cid, "expected_amount": 100})
    opp = int(db(f"select id from opportunities where title='{MARK}-商机' order by id desc limit 1"))
    call("POST", "/quotes", admin, {"opportunity_id": opp})
    qid = int(db(f"select id from quotes where opportunity_id={opp} order by id desc limit 1"))
    # 给 V1 加一条明细：提交审批要求"报价单有明细"（40003），没有明细就建不起
    # "待审批"这个夹具。用演示 SKU（带价格规则），明细才能带上价。
    # ⚠️ 必须**带成本**：不带成本毛利率按 100% 算，会命中种子里那条
    # 「高毛利小额自动免审」规则 —— 提交审批直接 auto_pass，待审批夹具就建不起来
    # （踩过：submit 返回 code=0「未超出权限，报价已通过」，然后查不到 pending）。
    # 成本 90 / 报价 100 → 毛利率约 10%，低于 25%，才会真的进审批流。
    demo_sku = db("select id from skus where deleted_at is null and status='active' "
                  "and sku_code not like 'CHK%' order by id limit 1")
    v1_id = int(db(f"select id from quote_versions where quote_id={qid} order by version_no limit 1"))
    if demo_sku:
        call("POST", f"/quote-versions/{v1_id}/items", admin,
             {"sku_id": int(demo_sku), "quantity": 1, "quoted_price": 100,
              "unit_cost": 90})
    check_true("夹具：V1 有明细（提交审批的前置）",
               int(db(f"select count(*) from quote_items where quote_version_id={v1_id}")) > 0)
    versions_before = int(db(f"select count(*) from quote_versions where quote_id={qid}"))
    status, res = call("POST", f"/quotes/{qid}/versions", admin, {})
    check_api("不带 confirm 被拒", res, CONFIRM_CODE)
    check("**没有执行**（版本数没变）",
          db(f"select count(*) from quote_versions where quote_id={qid}"), versions_before)
    msg = str(res.get("message"))
    check_true("文案说清会基于哪一版创建哪一版", "基于当前最新版" in msg, msg[:90])
    check_true("文案说清报价状态会改回草稿", "草稿" in msg)
    check_true("文案说清没有撤销入口", "撤销" in msg)
    status, res = call("POST", f"/quotes/{qid}/versions?confirm=true", admin, {})
    check_true("带 confirm 正常执行", res.get("code") == 0, f"code={res.get('code')}")
    check("版本数 +1",
          db(f"select count(*) from quote_versions where quote_id={qid}"), versions_before + 1)

    print("\n=== ①-b 旧版**还在待审批**时，文案必须点名会结束哪一版 ===")
    # 诊断：把夹具的中间值打出来 —— 前面踩过"SQL 拼出 `business_id, , 'pending'`"
    # 这种看不出原因的语法错，就是某个 id 悄悄为空造成的。
    print(
        f"  [诊断] 报价={qid} "
        f"版本数={db(f'select count(*) from quote_versions where quote_id={qid}')!r} "
        f"最新版id={db(f'select id from quote_versions where quote_id={qid} order by version_no desc limit 1')!r} "
        f"最新版no={db(f'select version_no from quote_versions where quote_id={qid} order by version_no desc limit 1')!r}"
    )
    # 这是主人特别强调的副作用：新建版本会 `close_superseded_approvals`，
    # 把旧版还在走的审批**自动结束**。
    #
    # ⚠️ 这里**直接插一条待审批实例**当夹具，不走 `submit-approval`。
    # 为什么（踩了很久）：`submit_for_approval` 会不会真的建审批实例，取决于
    # 提交人的**审批权限分档**和**免审规则**——
    #   · admin 权限够 → 返回「未超出权限，报价已通过」，压根没有待审批；
    #   · 毛利率 ≥25% 时命中种子里的「高毛利小额自动免审」→ auto_pass，也没有；
    #   · 换个低权限账号又会先被别的前置拦下。
    # 而本套件要验的判据很简单：**只要存在 pending 的审批实例，文案就要点名它**。
    # 所以直接把那条记录放进去，验的东西一点都不少，却不再依赖权限/规则配置。
    v2 = must_int("当前最新版本", f"select id from quote_versions where quote_id={qid} order by version_no desc limit 1")
    v2_no = db(f"select version_no from quote_versions where id={v2}")
    # ⚠️ 列名要对准模型（`app/modules/approval/model.py`）：
    #   · `definition_id` 是 **NOT NULL 外键**；
    #   · **没有 `updated_at` 列** —— 我照着别的表想当然写了一版，报
    #     `column "updated_at" does not exist`（`db()` 只把错误当字符串返回，
    #     不抛异常，所以那次失败是完全静默的）。
    #   · `summary` 用 null：判据只看 business_type/business_id/status。
    # 种子不建审批定义（真流程里是提交时动态创建的），所以这里补一条最小的。
    if not db("select id from approval_definitions order by id limit 1").isdigit():
        ins_def = db(
            "insert into approval_definitions (code, name, business_type, status, "
            "config_json, created_at) values "
            "('CHKCG_GATE', '确认闸门夹具', 'quote_version', 'active', null, now())"
        )
        check_true("夹具：补一条审批定义", ins_def == "" or ins_def.isdigit(),
                   f"返回={ins_def[:100]!r}")
    definition_id = must_int("审批定义", "select id from approval_definitions order by id limit 1")
    ins = db(
        "insert into approval_instances (definition_id, business_type, business_id, "
        "status, summary, created_at) values "
        f"({definition_id}, 'quote_version', {v2}, 'pending', null, now())"
    )
    check_true(f"夹具：插入待审批实例（版本 V{v2_no}）", ins == "" or ins.isdigit(),
               f"返回={ins[:120]!r}")
    pending_no = db(
        "select v.version_no from approval_instances a "
        "join quote_versions v on v.id = a.business_id "
        "where a.business_type='quote_version' and a.status='pending' "
        f"and v.quote_id={qid} order by v.version_no limit 1"
    )
    check_true("夹具：确实有待审批的版本", pending_no not in ("", "0"), f"V{pending_no}")
    check("夹具：待审批的就是当前最新版 V{v2_no}".replace("{v2_no}", str(v2_no)),
          pending_no, v2_no)
    print(f"  [诊断] 报价={qid} 待审批版本={pending_no}")
    status, res = call("POST", f"/quotes/{qid}/versions", admin, {})
    check_api("不带 confirm 被拒", res, CONFIRM_CODE)
    msg = str(res.get("message"))
    check_true(f"文案**点名**会结束 V{pending_no} 的待审批",
               f"V{pending_no}" in msg and "待审批" in msg, msg[:130])
    # 未确认时不能真的把审批结束掉
    check("**没有执行**（该版仍是待审批）",
          db(f"select status from approval_instances where business_type='quote_version' "
             f"and business_id={v2} order by id desc limit 1"), "pending")

    print("\n=== ② 审批规则发布：必须确认 ===")
    rid = int(db("select id from approval_rules order by id limit 1"))
    published_before = db(f"select published_version_no from approval_rules where id={rid}")
    status, res = call("POST", f"/approval-rules/{rid}/publish", admin, {})
    check_api("不带 confirm 被拒", res, CONFIRM_CODE)
    check("**没有执行**（发布版本号没变）",
          db(f"select published_version_no from approval_rules where id={rid}"), published_before)
    msg = str(res.get("message"))
    check_true("文案列出规则名与生效方式", "规则" in msg and "新规则" in msg, msg[:80])
    check_true("文案把条件写成可读中文（≥/≤ 而不是 gte）",
               ("≥" in msg or "≤" in msg or "属于" in msg), msg[:100])
    status, res = call("POST", f"/approval-rules/{rid}/publish?confirm=true", admin, {})
    check_true("带 confirm 正常执行", res.get("code") == 0, f"code={res.get('code')}")

    print("\n=== ③ 转销售订单：必须确认 ===")
    vid = int(db(f"select id from quote_versions where quote_id={qid} order by id desc limit 1"))
    orders_before = db(f"select count(*) from sales_orders where customer_id={cid}")
    status, res = call("POST", f"/quote-versions/{vid}/convert-to-order", admin, {})
    check_api("不带 confirm 被拒", res, CONFIRM_CODE)
    check("**没有执行**（订单数没变）",
          db(f"select count(*) from sales_orders where customer_id={cid}"), orders_before)
    msg = str(res.get("message"))
    check_true("文案列出订单金额", "订单金额" in msg, msg[:90])
    check_true("文案列出会生成的应收", "应收" in msg)

    print("\n=== ④ 推送 ERP/MES：必须确认 ===")
    sku = int(db("select id from skus where deleted_at is null and status='active' order by id limit 1"))
    call("POST", "/orders", admin,
         {"customer_id": cid, "currency": "CNY",
          "items": [{"sku_id": sku, "quantity": 1, "unit_price": 100}]})
    oid = int(db(f"select id from sales_orders where customer_id={cid} order by id desc limit 1"))
    pushed_before = db(f"select coalesce(erp_order_id,'') from sales_orders where id={oid}")
    status, res = call("POST", f"/orders/{oid}/sync-erp", admin, {})
    check_api("不带 confirm 被拒", res, CONFIRM_CODE)
    check("**没有执行**（erp_order_id 没变）",
          db(f"select coalesce(erp_order_id,'') from sales_orders where id={oid}"), pushed_before)
    msg = str(res.get("message"))
    check_true("文案列出订单号", "SO" in msg, msg[:90])
    check_true("文案列出客户", "客户" in msg)
    check_true("文案列出金额", "金额" in msg)
    check_true("文案说清撤回不了", "撤回" in msg)

    print("\n=== ⑤ 样品客户接受 / 未通过：都必须确认 ===")
    call("POST", "/samples", admin,
         {"customer_id": cid, "items": [{"sku_id": sku, "quantity": 1}]})
    sid = db(f"select coalesce(max(id),0) from sample_requests where customer_id={cid}")
    if int(sid) > 0:
        for accepted, label in ((True, "客户接受"), (False, "客户未通过")):
            status, res = call("POST", f"/samples/{sid}/confirm", admin,
                               {"accepted": accepted})
            check_api(f"{label}：不带 confirm 被拒", res, CONFIRM_CODE)
            check(f"{label}：**没有执行**（确认结果仍是空）",
                  db(f"select coalesce(customer_confirmed_at::text,'') from sample_requests where id={sid}"),
                  "")
            check_true(f"{label}：文案说清结果与商务事实",
                       "商务事实" in str(res.get("message")),
                       str(res.get("message"))[:70])
    else:
        print("  （样品建不出来，跳过）")

    print("\n=== ⑥ 对照：不带确认的普通动作不受影响（不能把闸门做滥）===")
    # 用**全新 SKU**：上面那个 `sku` 已经挂了规则，再建会撞唯一约束 40901 ——
    # 那是"重复"，不是"需要确认"，会把这条对照验成假绿。
    call("POST", "/products", admin,
         {"name": f"{MARK}-对照产品", "product_line": "x", "category": "包装"})
    pid2 = int(db(f"select id from products where name='{MARK}-对照产品' order by id desc limit 1"))
    call("POST", f"/products/{pid2}/skus", admin,
         {"sku_code": f"{MARK}-NC", "name": "对照件", "specification": "1×1", "unit": "个"})
    sku_nc = int(db(f"select id from skus where sku_code='{MARK}-NC'"))
    status, res = call("POST", "/price-rules", admin,
                       {"sku_id": sku_nc, "min_qty": 0, "standard_price": 120,
                        "guide_price": 110, "minimum_price": 90,
                        "effective_from": "2026-01-01"})
    check_true("建价格规则不需要确认（它不是那五类动作）", res.get("code") == 0,
               f"code={res.get('code')} {str(res.get('message'))[:40]}")
    status, res = call("POST", f"/quote-versions/{vid}/recalculate", admin, {})
    check_true("重算不需要确认", res.get("code") == 0, f"code={res.get('code')}")

    _cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"需要确认的动作：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
