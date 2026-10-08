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
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.user.model import User

if not os.environ.get("API_BASE"):
    raise SystemExit(
        "必须显式设置 API_BASE（不能依赖默认的 8000，那是开发后端）：\n"
        "  API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\\n"
        "    PYTHONPATH=. .venv/bin/python scripts/check_contract_archive_and_fx.py"
    )
BASE = os.environ["API_BASE"].rstrip("/")

# 外币夹具：造 EUR 报价前要先把业务口径放开，跑完收回（见 scripts/_fx_scope.py）
from _fx_scope import open_export, restore_domestic

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

        # 外汇数据要先把业务口径放开（业务方也是先改口径、再报外币价）。
        # `finally` 一定要收回：收不回去，后面所有套件都会以为可以写外币。
        open_export(admin)
        try:
            # ------------------------------------------------- 11.4 汇率生效时点
            print("\n=== 4. 汇率：只取报价时刻已生效的那条 ===")
            now = datetime.now(UTC)
            live = api(
                "POST",
                "/exchange-rates",
                {
                    "base_currency": "CNY",
                    "quote_currency": "EUR",
                    "rate": 7,
                    "source": f"{MARKER}当前生效",
                    "effective_at": (now - timedelta(days=1)).isoformat(),
                },
            )
            rate_ids.append(live["id"])
            future = api(
                "POST",
                "/exchange-rates",
                {
                    "base_currency": "CNY",
                    "quote_currency": "EUR",
                    "rate": 99,
                    "source": f"{MARKER}未来生效",
                    "effective_at": "2030-01-01T00:00:00+00:00",
                },
            )
            rate_ids.append(future["id"])

            quote = api(
                "POST",
                "/quotes",
                {"opportunity_id": opportunity_id, "customer_id": customer_id, "currency": "EUR"},
            )
            quote_ids.append(quote["quote_id"])
            async with SessionLocal() as session:
                version = (
                    await session.execute(
                        select(QuoteVersion)
                        .where(QuoteVersion.quote_id == quote["quote_id"])
                        .order_by(QuoteVersion.id.desc())
                        .limit(1)
                    )
                ).scalars().first()
                snapshot = version.exchange_rate_snapshot if version else None
                source = version.exchange_rate_source if version else None
            check(
                "★报价快照取的是「当前已生效」那条的 7，**不是** 2030 年那条的 99",
                snapshot is not None and Decimal(snapshot) == Decimal("7"),
                f"snapshot={snapshot} source={source}",
            )
            check(
                "接口响应里给前端看的快照也是 7（不是只在库里对）",
                Decimal(str(quote.get("exchange_rate_snapshot"))) == Decimal("7"),
                quote.get("exchange_rate_snapshot"),
            )
            check(
                "来源也没指向那条未来记录",
                source is not None and "未来生效" not in str(source),
                source,
            )

            # 构造"只有未来汇率"：把该币种**当前已生效的每一条**都推到未来。
            # 不能只动本套件建的那条 —— 库里可能还有别的套件 / 更早一次跑留下的同币种
            # 已生效汇率（本轮实打实踩到：一条残留让"只剩未来"这个场景根本构造不出来，
            # 报价照样 200）。原值记下来，用例结束恢复。
            async with SessionLocal() as session:
                pushed = [
                    (r.id, r.effective_at)
                    for r in (
                        await session.execute(
                            select(ExchangeRate).where(
                                ExchangeRate.quote_currency == "EUR",
                                ExchangeRate.effective_at <= datetime.now(UTC),
                            )
                        )
                    ).scalars().all()
                ]
                for rid, _at in pushed:
                    row = await session.get(ExchangeRate, rid)
                    if row is not None:
                        row.effective_at = datetime(2032, 1, 1, tzinfo=UTC)
                await session.commit()
            status, res = call(
                "POST",
                "/quotes",
                token=admin,
                body={"opportunity_id": opportunity_id, "customer_id": customer_id, "currency": "EUR"},
            )
            check("★只剩未来汇率时 → 拒绝报价（不许提前使用）", status == 422, status)
            check(
                "理由说清是「还没有生效的汇率」",
                "还没有生效的汇率" in str(res.get("message", "")),
                res.get("message"),
            )

            # 恢复原时间戳，并把本套件那条改成"过去生效"（过去也算已生效）
            async with SessionLocal() as session:
                for rid, at in pushed:
                    row = await session.get(ExchangeRate, rid)
                    if row is not None:
                        row.effective_at = at
                row = await session.get(ExchangeRate, live["id"])
                row.effective_at = datetime.now(UTC) - timedelta(days=30)
                await session.commit()
            quote2 = api(
                "POST",
                "/quotes",
                {"opportunity_id": opportunity_id, "customer_id": customer_id, "currency": "EUR"},
            )
            quote_ids.append(quote2["quote_id"])
            async with SessionLocal() as session:
                version2 = (
                    await session.execute(
                        select(QuoteVersion)
                        .where(QuoteVersion.quote_id == quote2["quote_id"])
                        .order_by(QuoteVersion.id.desc())
                        .limit(1)
                    )
                ).scalars().first()
            check(
                "过去生效的汇率照常可用（没把「过去」误伤成「未来」）",
                Decimal(version2.exchange_rate_snapshot) == Decimal("7"),
                version2.exchange_rate_snapshot,
            )
        finally:
            restore_domestic(admin)

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
