"""第十二批六条返修：真实接口反例、正向对照及写库不变断言。

复用阶段/明细套件的请求和隔离库夹具工具；只允许一次性库及本机独立 API。
"""

import sys
from decimal import Decimal

import check_opportunity_stage_item_guards as h

h.PREFIX = "CHK12R"


def execute(sql, params=None):
    from sqlalchemy import text

    async def go(session):
        await session.execute(text(sql), params or {})
        await session.commit()

    h.run(go)


def snapshot():
    """接口失败不能改变业务行、阶段历史、明细或成功审计。"""
    return tuple(h.db(sql) for sql in (
        "select id, stage_id, status, owner_id, title from opportunities order by id",
        "select id, from_stage_id, to_stage_id, entered_at, left_at from opportunity_stage_history order by id",
        "select id, quantity, target_price, remark from opportunity_items order by id",
        "select id from audit_logs where business_type='opportunity' "
        "and action in ('create','clone','change_stage','create_item','update_item','replace_items') order by id",
    ))


def rejected(label, method, path, token, body, status):
    before = snapshot()
    actual, response = h.call(method, path, token, body)
    h.check(label, actual, status)
    h.check_true(label + "：业务数据和成功审计不变", snapshot() == before)
    return response


def create(admin, suffix, **values):
    status, response = h.call("POST", "/opportunities", admin, {
        "customer_id": h.FIX["cust"], "title": h.PREFIX + suffix, **values,
    })
    h.check("新建 " + suffix, status, 200)
    return response["data"]


def stages(admin):
    _, response = h.call("GET", "/opportunity-stages", admin)
    rows = response["data"]
    for terminal in [s for s in rows if s["is_win"] or s["is_loss"]]:
        rejected("12-R1 显式终态新建", "POST", "/opportunities", admin, {
            "customer_id": h.FIX["cust"], "title": h.PREFIX + terminal["code"],
            "stage_id": terminal["id"],
        }, 422)
    for missing in (0, 999999999):
        rejected("12-R1 不存在阶段", "POST", "/opportunities", admin, {
            "customer_id": h.FIX["cust"], "title": h.PREFIX + "missing",
            "stage_id": missing,
        }, 404)
    status, response = h.call("POST", "/opportunity-stages", admin, {
        "code": h.PREFIX + "_ACTIVE", "name": h.PREFIX + "历史阶段", "sequence": 50,
    })
    h.check("新建普通阶段", status, 200)
    stage = response["data"]
    opportunity = create(admin, "显式普通阶段", stage_id=stage["id"])
    h.check("12-R1 普通阶段 status=open", opportunity["status"], "open")
    h.check("12-R1 允许显式选择非首阶段", opportunity["stage_id"], stage["id"])
    for _ in range(2):
        status, _ = h.call("DELETE", f"/opportunity-stages/{stage['id']}", admin)
        h.check("12-R2 停用幂等", status, 200)
    rejected("12-R2 停用阶段新建", "POST", "/opportunities", admin, {
        "customer_id": h.FIX["cust"], "title": h.PREFIX + "停用阶段",
        "stage_id": stage["id"],
    }, 422)
    for body in ({"stage_id": stage["id"]}, {"stage_code": stage["code"]}):
        rejected("12-R2 停用阶段推进", "POST", f"/opportunities/{h.FIX['opp']}/change-stage",
                 admin, body, 422)
    _, response = h.call("GET", f"/opportunities/{opportunity['id']}/stage-history", admin)
    h.check("12-R2 停用阶段历史名称", response["data"][0]["to_stage"], stage["name"])
    status, _ = h.call("POST", f"/opportunities/{opportunity['id']}/change-stage", admin,
                       {"stage_id": h.FIX["stage"]})
    h.check("12-R2 原停用阶段商机可推进到合法阶段", status, 200)
    default = create(admin, "默认阶段")
    h.check_true("默认排除停用及终态", default["stage_id"] != stage["id"] and
                 not next(s for s in rows if s["id"] == default["stage_id"])["is_win"])


def items(admin):
    oid, sku = h.FIX["opp"], h.FIX["sku"]
    status, response = h.call("POST", f"/opportunities/{oid}/items", admin,
                              {"sku_id": sku, "quantity": "2.125", "target_price": "1.2345"})
    h.check("12-R3 合法精度新增", status, 200)
    item = response["data"]["id"]
    response = rejected("12-R3 quantity=null", "PATCH", f"/opportunity-items/{item}", admin,
                        {"quantity": None}, 400)
    h.check_true("12-R3 提示明确数量不能为空", "数量" in response["message"] and "不能为空" in response["message"])
    status, _ = h.call("PATCH", f"/opportunity-items/{item}", admin, {"remark": "只改备注"})
    h.check("12-R3 不传数量可编辑备注", status, 200)
    h.check("12-R3 原数量精度不变", h.db("select quantity from opportunity_items where id=:i", {"i": item})[0][0], Decimal("2.125"))
    status, _ = h.call("PATCH", f"/opportunity-items/{item}", admin, {"target_price": None})
    h.check("12-R3 目标价允许清空", status, 200)
    h.check("12-R3 清空确实入库", h.db("select target_price from opportunity_items where id=:i", {"i": item})[0][0], None)
    for field, bad_values in (("quantity", [None, 0, -1, "NaN", "Infinity", "1e17", "0.0001", "1.23456"]),
                              ("target_price", [-1, "NaN", "Infinity", "1e17", "1.23456"])):
        for value in bad_values:
            rejected(f"12-R3 编辑 {field}={value}", "PATCH", f"/opportunity-items/{item}", admin,
                     {field: value}, 400)
            body = {"sku_id": sku, "quantity": "2.125", field: value}
            rejected(f"12-R3 新增 {field}={value}", "POST", f"/opportunities/{oid}/items", admin, body, 400)
            rejected(f"12-R3 批量 {field}={value}", "POST", f"/opportunities/{oid}/items/batch", admin,
                     {"items": [{"sku_id": sku, "quantity": 1}, body]}, 400)


def owners(admin, me):
    admin_name = h.db("select name from users where id=:i", {"i": h.FIX["admin"]})[0][0]
    assigned = create(me, "员工分管理员", owner_id=h.FIX["admin"])
    h.check("12-R6 响应负责人 ID", assigned["owner_id"], h.FIX["admin"])
    h.check("12-R6 响应负责人姓名", assigned["owner_name"], admin_name)
    _, detail = h.call("GET", f"/opportunities/{assigned['id']}", admin)
    _, listing = h.call("GET", f"/opportunities?customer_id={h.FIX['cust']}", admin)
    listed = next(row for row in listing["data"]["items"] if row["id"] == assigned["id"])
    h.check("12-R6 新建详情列表一致", [assigned["owner_name"], detail["data"]["owner_name"], listed["owner_name"]], [admin_name] * 3)
    inherited = create(admin, "管理员代建默认继承")
    me_name = h.db("select name from users where id=:i", {"i": h.FIX["me"]})[0][0]
    h.check("12-R6 默认继承客户负责人", (inherited["owner_id"], inherited["owner_name"]), (h.FIX["me"], me_name))
    own = create(me, "分给自己", owner_id=h.FIX["me"])
    h.check("12-R6 分给自己姓名", own["owner_name"], me_name)
    h.check("12-R6 创建人未被负责人覆盖", h.db("select created_by from opportunities where id=:i", {"i": assigned["id"]})[0][0], h.FIX["me"])
    h.check("12-R6 审计仍是操作者", h.db("select operator_id from audit_logs where business_type='opportunity' and business_id=:i and action='create'", {"i": assigned["id"]})[0][0], h.FIX["me"])
    # 停用来源负责人，但不改变原商机归属；另选在职员工仍可跨部门复制。
    execute("update opportunities set owner_id=:owner where id=:i", {"owner": h.FIX["retired"], "i": h.FIX["opp"]})
    for body in ({}, {"owner_id": h.FIX["retired"]}, {"owner_id": 999999999}):
        rejected("12-R5 默认或显式非法负责人", "POST", f"/opportunities/{h.FIX['opp']}/clone", admin,
                 {"title": h.PREFIX + "非法复制", **body}, 404 if body.get("owner_id") == 999999999 else 422)
    status, response = h.call("POST", f"/opportunities/{h.FIX['opp']}/clone", admin,
                              {"title": h.PREFIX + "改选在职复制", "owner_id": h.FIX["admin"]})
    h.check("12-R5 改选在职负责人成功", status, 200)
    h.check("12-R5 原商机历史归属不改", h.db("select owner_id from opportunities where id=:i", {"i": h.FIX["opp"]})[0][0], h.FIX["retired"])
    status, response = h.call("POST", f"/opportunities/{own['id']}/clone", admin, {"title": h.PREFIX + "在职默认复制"})
    h.check("12-R5 在职默认继承成功", status, 200)
    h.check("12-R5 在职默认继承 ID", response["data"]["owner_id"], h.FIX["me"])
    rejected("12-R5 范围外来源不可复制", "POST", f"/opportunities/{assigned['id']}/clone", me,
             {"title": h.PREFIX + "越权来源"}, 403)


def followups(admin, me):
    # 无跟进权限的概览必须明确 blocked，不能伪装为 0。
    oid = create(admin, "协作跟进", owner_id=h.FIX["me"])["id"]
    _, response = h.call("GET", f"/opportunities/{oid}/overview", me)
    h.check("12-R4 无模块权限计数为 null", response["data"]["counts"]["followups"], None)
    h.check_true("12-R4 无模块权限标记 blocked", "followups" in response["data"]["blocked"])
    execute("insert into role_permissions(role_id,permission_id) select r.id,p.id from roles r cross join permissions p "
            "where r.code=:r and p.code in ('followup:view','quote:view','order:view') on conflict do nothing", {"r": h.PREFIX + "_SELF"})
    me = h.login(h.PREFIX + "_self", "123456")
    visible_ids = []
    for number in range(7):
        status, response = h.call("POST", "/followups", admin, {
            "customer_id": h.FIX["cust"], "opportunity_id": oid,
            "content": h.PREFIX + f"管理员协作{number}", "exemption_reason": "waiting_external",
        })
        h.check("12-R4 管理员真实入口创建协作跟进", status, 200)
        visible_ids.append(response["data"]["followup"]["id"])
    # 来源单据属于管理员，业务员虽能看客户和商机，仍不能看这些系统事实。
    async def hidden_fixtures(session):
        from app.modules.followup.model import FollowUp
        from app.modules.quote.model import Quote
        from app.modules.order.model import SalesOrder
        from app.modules.customer.model import Customer
        quote = Quote(quote_no=h.PREFIX + "Q", customer_id=h.FIX["cust"], opportunity_id=oid, owner_id=h.FIX["admin"])
        order = SalesOrder(order_no=h.PREFIX + "O", customer_id=h.FIX["cust"], opportunity_id=oid, owner_id=h.FIX["admin"])
        outside = Customer(name=h.PREFIX + "范围外", owner_id=h.FIX["admin"])
        session.add_all([quote, order, outside])
        await session.flush()
        records = [FollowUp(customer_id=h.FIX["cust"], opportunity_id=oid, quote_id=quote.id,
                            owner_id=h.FIX["admin"], followup_type="系统", content=h.PREFIX + "报价系统"),
                   FollowUp(customer_id=h.FIX["cust"], opportunity_id=oid, order_id=order.id,
                            owner_id=h.FIX["admin"], followup_type="系统", content=h.PREFIX + "订单系统"),
                   FollowUp(customer_id=outside.id, owner_id=h.FIX["admin"], content=h.PREFIX + "范围外跟进")]
        session.add_all(records)
        await session.commit()
        return [r.id for r in records], outside.id
    hidden, outside_customer = h.run(hidden_fixtures)
    for followup in visible_ids:
        status, _ = h.call("GET", f"/followups/{followup}", me)
        h.check("12-R4 协作详情可见", status, 200)
    _, listing = h.call("GET", f"/followups?opportunity_id={oid}&page_size=200", me)
    _, overview = h.call("GET", f"/opportunities/{oid}/overview", me)
    data = overview["data"]
    h.check("12-R4 列表与全部可见数量一致", listing["data"]["total"], 7)
    h.check("12-R4 概览计数取全部可见记录", data["counts"]["followups"], 7)
    recent = data["followups"]
    h.check("12-R4 权限过滤先于最近五条限制", [r["id"] for r in recent], list(reversed(visible_ids[-5:])))
    for followup in hidden:
        status, _ = h.call("GET", f"/followups/{followup}", me)
        h.check("12-R4 受限来源及范围外详情拒绝", status, 403)
    _, listing = h.call("GET", "/followups?page_size=200", me)
    h.check_true("12-R4 主列表无受限记录", not set(hidden) & {r["id"] for r in listing["data"]["items"]})
    rejected("12-R5 范围外目标客户不可复制", "POST", f"/opportunities/{oid}/clone", me,
             {"customer_id": outside_customer, "title": h.PREFIX + "越权目标"}, 403)


def cleanup():
    for sql in ("delete from followups where content like :p",
                "delete from quotes where quote_no like :p",
                "delete from sales_orders where order_no like :p"):
        execute(sql, {"p": h.PREFIX + "%"})
    h.cleanup()


def main():
    admin = h.login("admin", "admin123")
    cleanup()
    try:
        h.build_fixtures()
        me = h.login(h.PREFIX + "_self", "123456")
        stages(admin)
        items(admin)
        owners(admin, me)
        followups(admin, me)
    finally:
        cleanup()
    if h.FAILURES:
        print("失败：", ", ".join(h.FAILURES))
        return 1
    print("第十二批六条返修：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
