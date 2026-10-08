#!/usr/bin/env python
"""回款线：应收币种（11.1）、凭证删除的文件保护（11.2）、上传失败的磁盘清理（11.8）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 三条各守什么

- **11.1（第 2–5 节）**：按比例生成应收时，每一期必须**显式继承订单币种**。
  原先这条路径不写 `currency`，落到列默认的 CNY —— 美元订单分出来的几期全成了
  人民币，"金额看着对、代表的钱已经不同"；后面的回款又是跟着节点币种走的，
  于是错一路传到回款。手工新增那条路径一直是继承订单币种的，两条必须一致。
- **11.2（第 6–9 节）**：回款删凭证接入统一的文件保护。原先它直接
  `session.delete(FileRecord)` + 删磁盘文件，**通用删除那三道保护一道都没过** ——
  同一份文件先当回款凭证、又被关联成"合同已签文件"时，删凭证会让**已签合同的
  原件连带消失**（合同还显示"已签署"、已签文件列表却空了）。
- **11.8（第 10 节）**：回款凭证上传失败（登记/审计写入报错）时，磁盘上那份
  没被登记的文件要清掉。通用上传一直有这段补偿，回款这个专用入口漏了。

## 为什么新建套件

`payment` 这条链此前**没有任何专属套件**：`ls scripts/ | grep -i "pay|receiv|order"`
零命中，`ops/check_suites.txt` 里也没有。所以这里从零建。

## 断言写法上的两个刻意选择

- **"被拒"不只看返回码，还要数一遍库里的行数 / 磁盘文件是否还在**。
  拒绝也可能是"先写进去再抛错"，靠事务回滚兜住。
- **币种三条一起测（CNY / USD / EUR）**：只测 CNY 的话，`currency="CNY"` 与
  "不写这一行落到默认 CNY" 两种实现**结果完全一样**，断言会给出假绿。
  USD/EUR 才分得开。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      FILE_ROOT=data/iso-files-xxx PYTHONPATH=. .venv/bin/python \\
      scripts/check_payment_and_vouchers.py
"""

import asyncio
import io
import json
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import String, delete, func, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.file import storage
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.user.model import User

#: ⚠️ 必须**显式**给 API_BASE，不给就拒跑（会真建订单/回款/凭证夹具）。
# 地址与库的防呆统一收在 _test_support（判据只留一处）
BASE = require_api_base()

MARKER = f"CHKPAY{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}


def upload_voucher(payment_id: int, token: str, content: bytes, filename: str):
    """按 multipart 打 `/payments/{id}/voucher`（与真实上传同一条路）。"""
    boundary = "----CHKPAY"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + f"/payments/{payment_id}/voucher", data=body, method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}


def _call_later(method: str, path: str, *, token: str) -> tuple[threading.Thread, dict]:
    """把一次真实请求放到后台线程发，用来观察它"有没有卡住"。

    ⚠️ 持锁的那个事务必须开在**主事件循环**里（见第 9 节）。不能在子线程里
    `asyncio.run` 去用同一个 async engine：asyncpg 的连接绑定在创建它的循环上，
    换一个循环去用会直接失败或挂住 —— 那样"没卡住"是被自己搞出来的假绿。
    """
    slot: dict = {}

    def run() -> None:
        slot["result"] = call(method, path, token=token, body=None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, slot


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def file_row(file_id: int):
    async with SessionLocal() as session:
        return (
            await session.execute(select(FileRecord).where(FileRecord.id == file_id))
        ).scalars().first()


async def plan_currencies(order_id: int) -> list[str]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(ReceivablePlan.currency)
                .where(ReceivablePlan.order_id == order_id)
                .order_by(ReceivablePlan.id.asc())
            )
        ).scalars().all()
        return [str(r) for r in rows]


async def plan_sum(order_id: int) -> Decimal:
    async with SessionLocal() as session:
        total = (
            await session.execute(
                select(func.sum(ReceivablePlan.amount)).where(ReceivablePlan.order_id == order_id)
            )
        ).scalar_one()
        return Decimal(total or 0)


async def plan_count(order_id: int) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ReceivablePlan)
                    .where(ReceivablePlan.order_id == order_id)
                )
            ).scalar_one()
        )


def disk_exists(object_key: str) -> bool:
    return storage.absolute_path(object_key).exists()


async def main():
    admin = login("admin", "admin123")

    order_ids: list[int] = []
    customer_ids: list[int] = []
    file_ids: list[int] = []
    created_keys: list[str] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        # ------------------------------------------------------------ 夹具
        print("=== 0. 夹具 ===")
        async with SessionLocal() as session:
            admin_id = int(
                (await session.execute(select(User.id).where(User.username == "admin"))).scalar_one()
            )
            customer = Customer(name=f"{MARKER}客户", owner_id=admin_id)
            session.add(customer)
            await session.flush()
            customer_ids.append(customer.id)

            # 每种币种**两张**订单：`M` 给"手工新增"、`G` 给"按比例生成"。
            # 为什么必须分开：生成接口见到该订单已有任何应收计划就拒
            # （"该订单已有应收计划，请先删除再重新生成"），同一张订单上跑不了两条路径。
            # 币种必须含外币：只测 CNY 时，"显式写 CNY"与"不写这行落到列默认 CNY"
            # 结果完全一样，断言会给出假绿（见文件头说明）。
            made: dict[str, dict[str, int]] = {}
            for code, amount in (("CNY", "1000.00"), ("USD", "100.00"), ("EUR", "200.00")):
                per_code: dict[str, int] = {}
                for tag in ("M", "G"):
                    order = SalesOrder(
                        order_no=f"{MARKER}-{code}-{tag}",
                        customer_id=customer.id,
                        owner_id=admin_id,
                        sales_owner_id=admin_id,
                        total_amount=Decimal(amount),
                        currency=code,
                        status="pending",
                        created_by=admin_id,
                    )
                    session.add(order)
                    await session.flush()
                    order_ids.append(order.id)
                    per_code[tag] = order.id
                made[code] = per_code
            await session.commit()
            print(
                f"  客户 {customer.id}、订单 "
                + " ".join(
                    f"{c}=手工{made[c]['M']}/生成{made[c]['G']}" for c in ("CNY", "USD", "EUR")
                )
            )

        cny_m, cny_g = made["CNY"]["M"], made["CNY"]["G"]
        usd_m, usd_g = made["USD"]["M"], made["USD"]["G"]
        eur_m, eur_g = made["EUR"]["M"], made["EUR"]["G"]

        # ---------------------------------------------- 11.1 手工新增应收
        print("\n=== 1. 手工新增应收：币种跟着订单走（三条路径的对照基线）===")
        for code, order_id in (("CNY", cny_m), ("USD", usd_m), ("EUR", eur_m)):
            row = api(
                "POST",
                f"/orders/{order_id}/receivables",
                {
                    "plan_name": f"{MARKER}手工{code}",
                    "due_date": "2026-11-10",
                    "amount": 10,
                },
            )
            check(f"手工新增（{code} 订单）→ 币种 {code}", row.get("currency") == code, row.get("currency"))

        # ---------------------------------------------- 11.1 按比例生成
        print("\n=== 2. 按比例生成：各期币种 = 订单币种（11.1 的核心）===")
        status, res = call(
            "POST",
            f"/orders/{usd_g}/receivables/generate",
            token=admin,
            body={
                "ratios": [0.3, 0.7],
                "first_due_date": "2026-11-20",
                "second_due_date": "2026-12-20",
                "first_name": "定金",
                "second_name": "尾款",
            },
        )
        check("USD 订单按 30/70 生成 → 200", status == 200, status)
        cur = await plan_currencies(usd_g)
        # 这张单上还有第 1 节手工建的一条（也是 USD），一起看
        check("USD 订单上所有应收期都是 USD（★修复点：原先落到 CNY）", cur == ["USD", "USD"], cur)
        check("各期金额之和 = 订单金额", str(await plan_sum(usd_g)) == "100.00", await plan_sum(usd_g))

        status, res = call(
            "POST",
            f"/orders/{eur_g}/receivables/generate",
            token=admin,
            body={
                "ratios": [0.5, 0.5],
                "first_due_date": "2026-11-20",
                "second_due_date": "2026-12-20",
            },
        )
        check("EUR 订单生成 → 200", status == 200, status)
        check(
            "EUR 订单上所有应收期都是 EUR",
            await plan_currencies(eur_g) == ["EUR", "EUR"],
            await plan_currencies(eur_g),
        )

        status, res = call(
            "POST",
            f"/orders/{cny_g}/receivables/generate",
            token=admin,
            body={"ratios": [1.0], "first_due_date": "2026-11-20"},
        )
        check("CNY 订单生成 → 200", status == 200, status)
        check(
            "内贸订单仍是 CNY（修复没把内贸改坏）",
            await plan_currencies(cny_g) == ["CNY"],
            await plan_currencies(cny_g),
        )

        print("\n=== 3. 同一请求键重试：只生成一组 ===")
        retry_order = None
        async with SessionLocal() as session:
            order = SalesOrder(
                order_no=f"{MARKER}-RETRY",
                customer_id=customer_ids[0],
                owner_id=admin_id,
                sales_owner_id=admin_id,
                total_amount=Decimal("50.00"),
                currency="USD",
                status="pending",
                created_by=admin_id,
            )
            session.add(order)
            await session.flush()
            retry_order = order.id
            order_ids.append(order.id)
            await session.commit()
        body = {
            "ratios": [0.4, 0.6],
            "first_due_date": "2026-11-25",
            "second_due_date": "2026-12-25",
            "request_key": f"{MARKER}-RK",
        }
        s1, r1 = call("POST", f"/orders/{retry_order}/receivables/generate", token=admin, body=body)
        s2, r2 = call("POST", f"/orders/{retry_order}/receivables/generate", token=admin, body=body)
        check("第一次生成 → 200", s1 == 200, s1)
        check("同键重试 → 照旧 200（回放原结果）", s2 == 200, s2)
        check("重试后库里仍只有 2 期（没有生成第二组）", await plan_count(retry_order) == 2, await plan_count(retry_order))
        check("重试生成的两期也是 USD", await plan_currencies(retry_order) == ["USD", "USD"], await plan_currencies(retry_order))

        # ---------------------------------------------- 11.1 回款币种
        print("\n=== 4. 回款登记的币种：跟着应收节点走；跨币种仍被拒 ===")
        usd_plan = api("GET", f"/receivables?order_id={usd_g}&page_size=50")["items"][0]
        check("应收节点本身是 USD（前提）", usd_plan["currency"] == "USD", usd_plan["currency"])
        row = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-21",
                "received_amount": 30,
            },
        )
        check("回款不传币种 → 继承节点，是 USD（不再是 CNY）", row["currency"] == "USD", row["currency"])

        status, res = call(
            "POST",
            "/payments",
            token=admin,
            body={
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-22",
                "received_amount": 10,
                "currency": "CNY",
            },
        )
        check("回款传「与节点不一致」的币种 → 仍被拒（跨币种限制没被绕过）", status == 422, status)

        # ---------------------------------------------- 11.2 只此一处引用
        print("\n=== 5. 凭证只被这一条回款用 → 正常删掉（记录与磁盘文件都清）===")
        p_clean = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-23",
                "received_amount": 5,
            },
        )["id"]
        status, res = upload_voucher(p_clean, admin, b"%PDF-1.4 clean", f"{MARKER}-clean.pdf")
        check("上传凭证 → 200", status == 200, status)
        clean_file_id = res["data"]["voucher_file_id"]
        file_ids.append(clean_file_id)
        clean_row = await file_row(clean_file_id)
        created_keys.append(clean_row.object_key)
        check("磁盘上确实有这份文件（前提）", disk_exists(clean_row.object_key) is True, True)

        status, res = call("DELETE", f"/payments/{p_clean}/voucher", token=admin)
        check("删凭证 → 200", status == 200, status)
        check("结果说明「文件本体一并清除」", res["data"]["file_deleted"] is True, True)
        check("文件记录真没了", await file_row(clean_file_id) is None, True)
        check("磁盘文件也没了", not disk_exists(clean_row.object_key), True)

        # ---------------------------------------------- 11.2 合同已签引用
        print("\n=== 6. 凭证同时是「已签合同扫描件」→ 拒绝删（合同文件不能消失）===")
        p_signed = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-24",
                "received_amount": 4,
            },
        )["id"]
        status, res = upload_voucher(p_signed, admin, b"%PDF-1.4 signed", f"{MARKER}-signed.pdf")
        check("上传凭证 → 200", status == 200, status)
        signed_file_id = res["data"]["voucher_file_id"]
        file_ids.append(signed_file_id)
        signed_row = await file_row(signed_file_id)
        created_keys.append(signed_row.object_key)

        # 把它关联成「合同已签文件」—— 与合同签署接口写的是同一行（category=signed）
        async with SessionLocal() as session:
            session.add(
                BusinessFile(
                    business_type="contract",
                    business_id=999999,  # 合同编号不必真实：本节点验的是「引用存在」
                    file_id=signed_file_id,
                    category="signed",
                )
            )
            await session.commit()

        status, res = call("DELETE", f"/payments/{p_signed}/voucher", token=admin)
        check("删凭证 → 被拒（422）", status == 422, status)
        check(
            "拒绝理由说清它是「已签署的原件」",
            "已签署的原件" in str(res.get("message", "")),
            res.get("message"),
        )
        check("★文件记录还在（合同的原件没消失）", await file_row(signed_file_id) is not None, True)
        check("★磁盘文件也还在", disk_exists(signed_row.object_key), True)
        check(
            "这条回款的凭证关联也**没被顺手清掉**（拒绝就是整体不生效）",
            api("GET", f"/payments/{p_signed}")["voucher_file_id"] == signed_file_id,
            signed_file_id,
        )

        # ---------------------------------------------- 11.2 别处普通引用
        print("\n=== 7. 凭证还被「普通业务附件」挂着 → 只解绑，文件不删 ===")
        p_shared = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-25",
                "received_amount": 3,
            },
        )["id"]
        status, res = upload_voucher(p_shared, admin, b"%PDF-1.4 shared", f"{MARKER}-shared.pdf")
        check("上传凭证 → 200", status == 200, status)
        shared_file_id = res["data"]["voucher_file_id"]
        file_ids.append(shared_file_id)
        shared_row = await file_row(shared_file_id)
        created_keys.append(shared_row.object_key)

        async with SessionLocal() as session:
            session.add(
                BusinessFile(
                    business_type="customer",
                    business_id=customer_ids[0],
                    file_id=shared_file_id,
                    category=None,
                )
            )
            await session.commit()

        status, res = call("DELETE", f"/payments/{p_shared}/voucher", token=admin)
        check("删凭证 → 200，但只是解绑", status == 200, status)
        check(
            "结果明确说「只解绑、文件没删」",
            res["data"] == {"detached_only": True, "file_deleted": False},
            res["data"],
        )
        check("文件记录留着（别处还在用）", await file_row(shared_file_id) is not None, True)
        check("磁盘文件也留着", disk_exists(shared_row.object_key), True)
        check(
            "这条回款的凭证关联已解除",
            api("GET", f"/payments/{p_shared}")["voucher_file_id"] is None,
            True,
        )

        # ---------------------------------------------- 11.2 已确认回款
        print("\n=== 8. 已确认 / 已驳回回款的凭证仍不能删（防回归）===")
        p_confirmed = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-26",
                "received_amount": 2,
            },
        )["id"]
        status, res = upload_voucher(p_confirmed, admin, b"%PDF-1.4 conf", f"{MARKER}-conf.pdf")
        conf_file_id = res["data"]["voucher_file_id"]
        file_ids.append(conf_file_id)
        conf_row = await file_row(conf_file_id)
        created_keys.append(conf_row.object_key)
        api("POST", f"/payments/{p_confirmed}/confirm", {"comment": "CHK"})
        status, res = call("DELETE", f"/payments/{p_confirmed}/voucher", token=admin)
        # 这条用的是**原有**判据（状态不允许，走全局约定的 400），
        # 与上面"文件受保护"那档的 422 是两回事，别混成一个码
        check("已确认的回款 → 删凭证仍是 400（状态不允许）", status == 400, status)
        check("凭证文件完好", await file_row(conf_file_id) is not None, True)

        # ---------------------------------------------- 11.2 并发
        print("\n=== 9. 并发：给文件加引用与删文件共用同一把锁 ===")
        p_race = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-27",
                "received_amount": 1,
            },
        )["id"]
        status, res = upload_voucher(p_race, admin, b"%PDF-1.4 race", f"{MARKER}-race.pdf")
        race_file_id = res["data"]["voucher_file_id"]
        file_ids.append(race_file_id)
        race_row = await file_row(race_file_id)
        created_keys.append(race_row.object_key)

        # 确定性复现"删除正在进行、事务还没提交"：在主事件循环里开一个独立事务
        # 锁住文件行，**先不提交**。此时**给这份文件加引用**的一方必须卡住。
        #
        # 为什么断言"加引用"而不断言"删除"：删除本身就要拿行的写锁，有没有这道
        # 显式锁都会等 —— 拿它当判据**没有区分度**（撤掉锁照样绿，实测过）。
        # 而 INSERT 一条 `business_files` **不碰 `files` 行**，所以不加锁时它会
        # 在"删除查完引用"之后立刻插进来，留下指向已删文件的悬空引用。
        locker = SessionLocal()
        await locker.execute(
            select(FileRecord.id).where(FileRecord.id == race_file_id).with_for_update()
        )
        try:
            attacher, slot = _call_later(
                "POST",
                f"/business/customer/{customer_ids[0]}/files?file_id={race_file_id}",
                token=admin,
            )
            time.sleep(2.0)
            check(
                "★给这份文件加引用的一方被挡在锁外（2 秒后仍未返回）",
                "result" not in slot,
                f"实际已返回：{slot.get('result')}",
            )
            await locker.commit()  # 放锁：模拟"删除那边提交了"
            attacher.join(timeout=20)
        finally:
            await locker.close()
        check(
            "放锁后关联完成 → 200",
            (slot.get("result") or (None, None))[0] == 200,
            slot.get("result"),
        )
        async with SessionLocal() as session:
            linked = (
                await session.execute(
                    select(func.count())
                    .select_from(BusinessFile)
                    .where(BusinessFile.file_id == race_file_id)
                )
            ).scalar_one()
        check("关联真的落库了", int(linked) == 1, int(linked))

        # ---------------------------------------------- 11.8 上传失败清理
        print("\n=== 10. 上传登记失败 → 磁盘不留「没有记录的文件」（11.8）===")
        p_fail = api(
            "POST",
            "/payments",
            {
                "receivable_plan_id": usd_plan["id"],
                "received_date": "2026-11-28",
                "received_amount": 6,
            },
        )["id"]
        await assert_upload_compensation(p_fail, admin_id)

    finally:
        print("\n=== 收尾清理 ===")
        async with SessionLocal() as session:
            # 先删子表（外键方向）：回款 → 应收 → 订单 → 客户
            await session.execute(
                delete(PaymentRecord).where(PaymentRecord.order_id.in_(order_ids))
            )
            await session.execute(
                delete(ReceivablePlan).where(ReceivablePlan.order_id.in_(order_ids))
            )
            # 文件：先删关联，再删记录
            await session.execute(
                delete(BusinessFile).where(BusinessFile.file_id.in_(file_ids))
            )
            # 本节点自造的「合同已签文件」关联（business_id 是假编号）
            await session.execute(
                delete(BusinessFile).where(BusinessFile.business_id == 999999)
            )
            await session.execute(delete(FileRecord).where(FileRecord.id.in_(file_ids)))
            await session.execute(delete(SalesOrder).where(SalesOrder.id.in_(order_ids)))
            await session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            # 审计：按内容带本套件标记兜底收
            await session.execute(
                delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
            )
            await session.execute(
                delete(AuditLog).where(AuditLog.before_data.cast(String).contains(MARKER))
            )
            await session.commit()
        # 磁盘：按**本次记下的 object_key** 删；顺带把本套件前缀命名的残留也扫一遍
        for key in created_keys:
            storage.delete_object(key)
        print(f"  已清：订单 {len(order_ids)} 张、客户 {len(customer_ids)} 个、文件 {len(file_ids)} 份")

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 回款线：按比例生成继承订单币种（CNY/USD/EUR 三路径一致、合计等于总额、"
        "同键重试只生成一组、跨币种回款仍被拒）+ 凭证删除接入统一文件保护"
        "（独占可删 / 已签原件拒绝 / 别处引用只解绑 / 已确认不可删 / 并发串行化）+ "
        "上传失败不留孤儿文件"
    )


async def assert_upload_compensation(payment_id: int, admin_id: int) -> None:
    """在进程内调上传接口，并让审计写入报错 —— 验证磁盘不留「没被登记的文件」。

    为什么要进程内（不是 HTTP）：这段逻辑要的是"登记失败"这个分支，而它是
    另一个进程里的行为。走 HTTP 只能靠改代码造故障，代价更大。项目里
    `check_business_time_edges.py` 已有"进程内直接调路由函数"的先例。
    """
    from starlette.datastructures import UploadFile

    from app.core.deps import CurrentUser
    from app.modules.payment import router as payment_router

    payload = b"%PDF-1.4 doomed"
    before = set(_list_iso_files())

    original = payment_router.write_audit

    async def boom(*args, **kwargs):
        raise RuntimeError("模拟审计写入失败（CHKPAY）")

    payment_router.write_audit = boom
    raised = False
    try:
        async with SessionLocal() as session:
            user = CurrentUser(
                await _admin_row(session, admin_id), set(), ["admin"], "all"
            )
            try:
                await payment_router.upload_payment_voucher(
                    payment_id,
                    _fake_request(),
                    file=UploadFile(
                        file=io.BytesIO(payload), filename=f"{MARKER}-doomed.pdf"
                    ),
                    user=user,
                    session=session,
                )
            except Exception:  # noqa: BLE001 —— 这里就是要它失败
                raised = True
    finally:
        payment_router.write_audit = original

    check("上传在「审计写入失败」时确实报错（前提）", raised, True)
    after = set(_list_iso_files())
    new_files = after - before
    check(
        f"★磁盘没有留下「没有记录的文件」（新增 {len(new_files)} 份）",
        not new_files,
        sorted(new_files),
    )
    async with SessionLocal() as session:
        leaked = (
            await session.execute(
                select(func.count())
                .select_from(FileRecord)
                .where(FileRecord.file_name == f"{MARKER}-doomed.pdf")
            )
        ).scalar_one()
    check("库里也没有这条登记（事务确实回滚了）", int(leaked) == 0, int(leaked))


async def _admin_row(session, admin_id: int):
    return (await session.execute(select(User).where(User.id == admin_id))).scalars().one()


def _list_iso_files() -> list[str]:
    """隔离文件目录下的所有文件（相对 key 形式），用于比对"有没有多出文件"。"""
    root = storage.file_root()
    return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]


class _FakeRequest:
    """只要 `client_ip(request)` 能用即可。"""

    class _Client:
        host = "127.0.0.1"

    client = _Client()
    headers: dict = {}


def _fake_request() -> _FakeRequest:
    return _FakeRequest()


if __name__ == "__main__":
    asyncio.run(main())
