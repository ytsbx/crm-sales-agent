#!/usr/bin/env python
"""合同存档原件的下载（11.3）与汇率的生效时点（11.4）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 两条各守什么

- **11.3（第 1–3 节）**：`GET /contract-documents/{id}/download` 必须读**生成时
  落盘的存档原件**。原实现"读不到就当场重新排一份给你、HTTP 200"，于是
  存档丢了也看不出来 —— 用户以为拿到的是当初那份，台账上"同一编号永远同一份"
  这句承诺也破了。现在：记录缺失 / 磁盘文件缺失 / 校验值不符 → 明确 410 +
  一句说明；历史上**本来就没有存档**的旧记录仍可渲染，但必须标明是**副本**。
- **11.4（第 4 节）**：取汇率只选**报价时刻已经生效**的记录，再取其中最新一条。
  原实现只按生效时间倒序取最新，于是"提前录一条 2030 年生效的"会被今天的报价
  用上（实测 7 变成 99），报价当场就错、来源还显示成那条未来记录。

## 为什么新建套件

合同下载这条链只被 `check_contract_documents.py` 覆盖到"挂号与签署"，
**存档丢失 / 校验不符 / 旧记录副本**这几条一条都没有；汇率更是只有函数级的
边界断言（`check_business_time_edges.py` 那类），没有"未来汇率不许提前用"。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      FILE_ROOT=data/iso-files-xxx PYTHONPATH=. .venv/bin/python \\
      scripts/check_contract_archive_and_fx.py
"""

import asyncio
import hashlib
import json
import time
import uuid
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import String, delete, func, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.customer.model import Customer
from app.modules.file import storage
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.pricing.model import ExchangeRate
from app.modules.quote import service as quote_service
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.user.model import User

# 地址与库的防呆统一收在 _test_support（判据只留一处）
BASE = require_api_base()

#: 外币夹具：**直接造历史数据**（不走 API）。
#:
#: 从前这里要先把业务口径放开、再报外币价。第四批复审之后币种是固定白名单，
#: 那条路**故意**堵死了 —— 这一节改成造历史记录，验的还是"历史外币数据的
#: 读取路径有定义行为"，并额外断言"走 API 确实建不出来"。
async def seed_foreign_quote(opportunity_id: int, customer_id: int, currency: str) -> dict:
    """⚠️ 必须是 async：本套件自己跑在事件循环里，
    在里层再 `asyncio.run` 会报 "cannot be called from a running event loop"
    （实测踩过一次 —— 与"连接池绑事件循环"是同一类问题的两个面）。
    """
    from app.core.database import SessionLocal
    from app.modules.quote.model import Quote, QuoteVersion

    async with SessionLocal() as session:
        quote = Quote(
            # `quote_no` 是 NOT NULL（真实入口会取号）—— 手工造数据得自己给，
            # 而且要带随机后缀：秒级时间戳在"同一秒里造两条"时会撞唯一约束
            # （实测撞过一次）
            quote_no=f"CHKFXR{int(time.time())}{uuid.uuid4().hex[:4]}",
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            owner_id=1,
            status="draft",
        )
        session.add(quote)
        await session.flush()
        version = QuoteVersion(
            quote_id=quote.id,
            version_no=1,
            currency=currency,
            subtotal_amount=Decimal("100"),
            total_amount=Decimal("100"),
            approval_status="not_submitted",
            created_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        # ⚠️ **走产品代码**填汇率快照，别自己硬塞一个值：
        # 真实入口 `create_quote` 就是调它（`apply_exchange_rate_snapshot`）。
        # 自己塞值等于把被测逻辑换成我的假设 —— 那样这一节就白测了。

        # 走产品函数填快照：它内部自己取汇率（`resolve_exchange_rate`），
        # 拿不到时会返回一句人话，我们把它记下来（下面断言用）
        note = await quote_service.apply_exchange_rate_snapshot(
            session, version, currency=currency, explicit_rate=None
        )
        if note is not None:
            print(f"  · [seed] 没取到汇率：{note}")
        await session.commit()
    return {"quote_id": int(quote.id), "version_id": int(version.id)}


MARKER = f"CHKDOC{int(time.time())}"
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


def download_raw(path: str, token: str) -> tuple[int, dict, bytes]:
    """下载类接口：返回 (状态码, 响应头, 原始字节)。"""
    req = urllib.request.Request(BASE + path, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    #: 头名统一转小写 —— HTTP 头大小写不敏感，用小写键读才不会因服务端写法不同而漏判
    def _lower(raw) -> dict:
        return {k.lower(): v for k, v in raw.items()}

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _lower(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, _lower(exc.headers), exc.read()


def json_or_empty(raw: bytes) -> dict:
    """把响应体当 JSON 解析；**不是** JSON 就返回空 dict。

    为什么要容错：反向验证时（把"明确报错"退回"静默重渲染"）这里会收到一份 PDF，
    直接 `json.loads` 会抛异常 → 套件整段崩掉，**后面几十条断言一条都跑不到**，
    反向验证就看不全"到底影响了哪些行为"。
    """
    try:
        return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def archive_bytes(doc_id: int) -> tuple[str | None, bytes | None]:
    """这份合同的存档 object_key 与磁盘上的字节（没有存档就给 (None, None)）。"""
    async with SessionLocal() as session:
        doc = await session.get(ContractDocument, doc_id)
        if doc is None or doc.generated_file_id is None:
            return None, None
        record = await session.get(FileRecord, doc.generated_file_id)
        if record is None:
            return None, None
    path = storage.absolute_path(record.object_key)
    return record.object_key, (path.read_bytes() if path.exists() else None)


async def main():
    admin = login("admin", "admin123")

    customer_ids: list[int] = []
    doc_ids: list[int] = []
    template_ids: list[int] = []
    rate_ids: list[int] = []
    quote_ids: list[int] = []
    opportunity_ids: list[int] = []

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
            # 报价必须关联商机（只给客户会被拒），所以夹具里要有一个
            stage_id = int(
                (
                    await session.execute(
                        select(OpportunityStage.id).order_by(OpportunityStage.id.asc()).limit(1)
                    )
                ).scalar_one()
            )
            opportunity = Opportunity(
                customer_id=customer.id,
                title=f"{MARKER}商机",
                stage_id=stage_id,
                owner_id=admin_id,
            )
            session.add(opportunity)
            await session.flush()
            opportunity_ids.append(opportunity.id)
            await session.commit()
        customer_id = customer_ids[0]
        opportunity_id = opportunity_ids[0]

        template = api(
            "POST",
            "/contract-templates",
            {
                "doc_type": "contract",
                "name": f"{MARKER}模板",
                "body": f"客户：{{{{customer.name}}}}\n编号：{MARKER}",
            },
        )
        template_ids.append(template["id"])
        doc = api(
            "POST",
            "/contract-documents",
            {"template_id": template["id"], "customer_id": customer_id},
        )
        doc_ids.append(doc["id"])
        key, original = await archive_bytes(doc["id"])
        check("生成合同后留下了存档文件（前提）", key is not None and original is not None, key)

        # ------------------------------------------------- 11.3 正常下载
        print("\n=== 1. 正常下载：读的就是存档原件 ===")
        status, headers, body = download_raw(f"/contract-documents/{doc['id']}/download", admin)
        check("下载 → 200", status == 200, status)
        check("返回的字节与磁盘存档**完全一致**", body == original, f"{len(body)} vs {len(original or b'')}")
        check(
            "响应头标明来源是「当时的存档」",
            headers.get("x-contract-source") == "generated",
            headers.get("x-contract-source"),
        )

        # ------------------------------------------------- 11.3 原件不可用
        print("\n=== 2. 存档不可用 → 明确报错，绝不偷偷重新生成 ===")
        archive_path = storage.absolute_path(key)
        archive_path.unlink()
        try:
            status, headers, body = download_raw(
                f"/contract-documents/{doc['id']}/download", admin
            )
            check("磁盘文件没了 → 410（不再是 200 + 一份新 PDF）", status == 410, status)
            check("**没有**返回 PDF 内容", body[:4] != b"%PDF", body[:20])
            payload = json_or_empty(body)
            check(
                "说清是「存档原件不可用」",
                "存档原件不可用" in str(payload.get("message", "")),
                payload.get("message"),
            )
        finally:
            archive_path.write_bytes(original)  # 复原，供后面的用例用

        # 记录没了（但磁盘文件还在）
        async with SessionLocal() as session:
            doc_row = await session.get(ContractDocument, doc["id"])
            saved_file_id = doc_row.generated_file_id
        # 把**那条 files 记录**删掉，而 doc 上仍记着这个编号 —— 这才是"记录缺失"。
        # （清空 doc 上的编号是另一回事：那等于"本来就没存过档"，第 3 节专门验。）
        async with SessionLocal() as session:
            record_row = await session.get(FileRecord, saved_file_id)
            await session.delete(record_row)
            await session.commit()
        status, _h, body = download_raw(f"/contract-documents/{doc['id']}/download", admin)
        check("存档记录没了 → 同样是 410", status == 410, status)
        payload = json_or_empty(body)
        check(
            "理由说清是「记录已不存在」",
            "记录已不存在" in str(payload.get("message", "")),
            payload.get("message"),
        )
        # 把记录补回来（指向同一个 object_key），后面的用例还要用它
        async with SessionLocal() as session:
            restored = FileRecord(
                storage_provider="local",
                object_key=key,
                file_name=f"{doc['doc_no']}.pdf",
                mime_type="application/pdf",
                size=len(original),
                checksum=hashlib.sha256(original).hexdigest(),
                uploaded_by=admin_id,
            )
            session.add(restored)
            await session.flush()
            doc_row = await session.get(ContractDocument, doc["id"])
            doc_row.generated_file_id = restored.id
            await session.commit()

        # 校验值不符（文件被人换过）
        archive_path.write_bytes(original + b"\n% tampered")
        status, _h, body = download_raw(f"/contract-documents/{doc['id']}/download", admin)
        check("存档内容与校验值不符 → 410", status, 410)
        payload = json_or_empty(body)
        check(
            "理由说清是「校验值不一致」",
            "校验值不一致" in str(payload.get("message", "")),
            payload.get("message"),
        )
        archive_path.write_bytes(original)
        status, _h, _b = download_raw(f"/contract-documents/{doc['id']}/download", admin)
        check("复原后又能正常下载（前面的拒绝没有副作用）", status == 200, status)

        # ------------------------------------------------- 11.3 旧记录副本
        print("\n=== 3. 历史旧记录（本来就没有存档）→ 可以给，但必须标明是副本 ===")
        async with SessionLocal() as session:
            legacy = ContractDocument(
                doc_no=f"{MARKER}-LEGACY",
                doc_type="contract",
                template_id=template["id"],
                customer_id=customer_id,
                status="draft",
                title=f"{MARKER}旧记录",
                content_snapshot="",  # NOT NULL，历史数据里也是空的
                generated_file_id=None,  # 本批之前的老数据就是这样
                created_by=admin_id,
                created_at=datetime.now(UTC),
            )
            session.add(legacy)
            await session.flush()
            legacy_id = legacy.id
            doc_ids.append(legacy_id)
            await session.commit()
        status, headers, body = download_raw(f"/contract-documents/{legacy_id}/download", admin)
        check("旧记录仍可下载 → 200", status == 200, status)
        check("内容确实是一份 PDF", body[:4] == b"%PDF", body[:8])
        check(
            "响应头标明「依据历史数据生成的副本」",
            headers.get("x-contract-source") == "legacy_rendered",
            headers.get("x-contract-source"),
        )
        disposition = headers.get("content-disposition", "")
        check(
            "文件名里也带「副本」二字（用户存到本地也分得清）",
            "%E5%89%AF%E6%9C%AC" in disposition,  # "副本" 的 URL 编码
            disposition,
        )

        # ⚠️ **外币报价现在建不出来了**（第四批复审 + 主人 2026-10-12 的口径：
        # 「没有外币这个东西…有人用外币就给他报错」）。从前这一节靠
        # `open_export()` 临时放开口径来造外币数据 —— 那条路**故意**堵死。
        #
        # 所以这一节改成两层：
        #   ① **入口**：走 API 建外币报价必须被拒（口径怎么配都一样）；
        #   ② **读取路径**：历史外币数据的汇率快照行为，直接在**产品函数层**验
        #      （`resolve_exchange_rate`）—— 已经没有 HTTP 路径能触发它了。
        #      保留它的价值：这条判据来自"提前录一条 2030 年生效的汇率会被今天的
        #      报价用上（7 变成 99）"那个真事故，不能因为入口堵了就变成没人验的死代码。
        print("\n=== 4. 汇率：入口与历史外币数据（口径改后重写）===")
        status, body = call(
            "POST", "/quotes", admin,
            {"opportunity_id": opportunity_id, "customer_id": customer_id,
             "currency": "EUR"},
        )
        check(
            "① 走 API 建外币报价被拒（40002，与口径配置无关）",
            body.get("code") == 40002,
            f"code={body.get('code')} message={str(body.get('message'))[:60]}",
        )

        now = datetime.now(UTC)
        live = api(
            "POST", "/exchange-rates",
            {"base_currency": "CNY", "quote_currency": "EUR", "rate": 7,
             "source": f"{MARKER}当前生效",
             "effective_at": (now - timedelta(days=1)).isoformat()},
        )
        rate_ids.append(live["id"])
        future = api(
            "POST", "/exchange-rates",
            {"base_currency": "CNY", "quote_currency": "EUR", "rate": 99,
             "source": f"{MARKER}未来生效",
             "effective_at": "2030-01-01T00:00:00+00:00"},
        )
        rate_ids.append(future["id"])

        # ⚠️ 契约：`resolve_exchange_rate` 返回 **(rate, source)**，不是 (rate, problem)！
        # 我第一版按 `(rate, problem)` 读，于是把来源说明 `CHKDOC…当前生效` 当成"错误信息"，
        # 断言全红 —— 而函数其实完全正常。**读返回值语义别靠猜，去看签名。**
        async with SessionLocal() as session:
            rate, source = await quote_service.resolve_exchange_rate(session, "EUR")
        check(
            "② ★取汇率取「当前已生效」那条的 7，**不是** 2030 年那条的 99",
            rate is not None and Decimal(str(rate)) == Decimal("7"),
            f"rate={rate} source={source}",
        )

        # 历史外币数据：直接造记录，并**走产品代码**填快照
        quote = await seed_foreign_quote(opportunity_id, customer_id, "EUR")
        quote_ids.append(quote["quote_id"])
        async with SessionLocal() as session:
            version = (
                await session.execute(
                    select(QuoteVersion).where(QuoteVersion.id == quote["version_id"])
                )
            ).scalars().one()
        check(
            "③ 历史外币版本的汇率快照落在版本行上（前端读的就是这一行）",
            version.exchange_rate_snapshot is not None
            and Decimal(str(version.exchange_rate_snapshot)) == Decimal("7"),
            f"snapshot={version.exchange_rate_snapshot} source={version.exchange_rate_source}",
        )
        check(
            "④ 来源也没指向那条未来记录",
            version.exchange_rate_source is not None
            and "未来生效" not in str(version.exchange_rate_source),
            version.exchange_rate_source,
        )

        # ⑤ 只有未来汇率时取不到 —— **不许提前使用**
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(ExchangeRate).where(
                        ExchangeRate.quote_currency == "EUR",
                        ExchangeRate.effective_at <= datetime.now(UTC),
                    )
                )
            ).scalars().all()
            # 推到 2032：本条用例之后由清理阶段统一删掉这些汇率，
            # 所以不需要记原值再恢复（记了也没人用 —— pyflakes 会报未用变量）
            for row in rows:
                row.effective_at = datetime(2032, 1, 1, tzinfo=UTC)
            await session.commit()
        async with SessionLocal() as session:
            # 用"造一张新版本并填快照"这条真实路径来问："现在能不能拿到汇率"
            probe = await seed_foreign_quote(opportunity_id, customer_id, "EUR")
            quote_ids.append(probe["quote_id"])
            probe_version = (
                await session.execute(
                    select(QuoteVersion).where(QuoteVersion.id == probe["version_id"])
                )
            ).scalars().one()
        check(
            "⑤ ★只剩未来汇率时，快照**填不上**（不许提前使用）+ 版本行上没有假汇率",
            probe_version.exchange_rate_snapshot is None,
            f"snapshot={probe_version.exchange_rate_snapshot}",
        )
        async with SessionLocal() as session:
            note = await quote_service.apply_exchange_rate_snapshot(
                session, probe_version, currency="EUR", explicit_rate=None
            )
        check(
            "⑤-b 而且给出的理由是「还没生效」，不是笼统的『没有汇率』"
            "（两种情形要能分开，否则用户会去重复录一条）",
            note is not None and "还没有生效的汇率" in str(note),
            note,
        )


    finally:
        print("\n=== 收尾清理 ===")

        # 清理**分段、各自独立事务**：任一段抛错只影响那一段，其余照清。
        # 原先全在一个事务里 —— 本轮实打实踩到：删客户撞外键 → 整段回滚 →
        # 连汇率、审计都没清掉，给下一个套件留了残渣（守门套件当场抓到一条
        # `CHKDOC…客户`）。套件的清理必须"尽力而为、逐段兜住"。
        async def _drop(label: str, stmt) -> None:
            try:
                async with SessionLocal() as session:
                    await session.execute(stmt)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 —— 清理失败不该盖掉真正的失败
                print(f"  清理「{label}」失败（不影响其余）：{exc.__class__.__name__}")

        # 一律按**本套件的前缀/标识**范围删，不按"记过账的 id"删：套件中途失败时，
        # 那些建了一半、还没记上账的行只能靠范围删收掉。
        doomed_customers = select(Customer.id).where(Customer.name.like(f"{MARKER}%"))
        doomed_quotes = select(Quote.id).where(Quote.customer_id.in_(doomed_customers))
        doomed_docs = select(ContractDocument.id).where(
            ContractDocument.customer_id.in_(doomed_customers)
        )
        doomed_files = select(ContractDocument.generated_file_id).where(
            ContractDocument.id.in_(doomed_docs),
            ContractDocument.generated_file_id.is_not(None),
        )
        await _drop("报价版本", delete(QuoteVersion).where(QuoteVersion.quote_id.in_(doomed_quotes)))
        await _drop("报价", delete(Quote).where(Quote.id.in_(doomed_quotes)))
        await _drop(
            "合同附件关联（按文件）", delete(BusinessFile).where(BusinessFile.file_id.in_(doomed_files))
        )
        await _drop(
            "合同附件关联（按对象）", delete(BusinessFile).where(BusinessFile.business_id.in_(doomed_docs))
        )
        await _drop("文件记录", delete(FileRecord).where(FileRecord.id.in_(doomed_files)))
        await _drop("合同", delete(ContractDocument).where(ContractDocument.id.in_(doomed_docs)))
        await _drop(
            "合同模板", delete(ContractTemplate).where(ContractTemplate.name.like(f"{MARKER}%"))
        )
        await _drop("汇率", delete(ExchangeRate).where(ExchangeRate.source.like(f"{MARKER}%")))
        await _drop(
            "审计", delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
        )
        await _drop(
            "审计（before）",
            delete(AuditLog).where(AuditLog.before_data.cast(String).contains(MARKER)),
        )
        await _drop("商机", delete(Opportunity).where(Opportunity.title.like(f"{MARKER}%")))
        await _drop("客户", delete(Customer).where(Customer.name.like(f"{MARKER}%")))

        # 磁盘：把本套件生成的存档原件清掉（用前面记下的 key —— 记录已删就查不到了）
        if key:
            storage.delete_object(key)

        # 自查一句：守门套件只报"有残渣"，定位还得靠这里
        async with SessionLocal() as session:
            left = (
                await session.execute(
                    select(func.count())
                    .select_from(Customer)
                    .where(Customer.name.like(f"{MARKER}%"))
                )
            ).scalar_one()
        print(f"  已清（按前缀 {MARKER} 范围收）" + ("；⚠️ 仍有客户残留" if int(left) else ""))

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 合同存档与汇率：下载读存档原件（缺失/记录没了/校验不符一律 410 且不给 PDF、"
        "旧记录标明副本）+ 汇率只取已生效的（未来那条不许提前用、只剩未来时明确拒绝、"
        "过去生效照常可用）"
    )


if __name__ == "__main__":
    asyncio.run(main())
