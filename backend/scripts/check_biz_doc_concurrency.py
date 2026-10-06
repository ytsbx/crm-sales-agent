"""第八批 §8.9 的**真 PostgreSQL 并发**验证（SQLite 验不了锁语义）。

为什么必须单独有这个脚本：并发缺陷是"两个连接同时读、同时写"才出现的，
内存 SQLite 是单写者，用例里怎么排都复现不出来。这里的每个探针都
**真的开两条独立连接**（各自一个 `AsyncSession`），用事件对齐后同时发起。

跑法（仓库根；库名必须是 crm_iso / crm_check 开头的一次性库）：

    docker exec crm-postgres psql -U crm -d postgres -c "CREATE DATABASE crm_iso_bd2;"
    cd backend
    $env:DATABASE_URL='postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_bd2'
    .\\.venv\\Scripts\\python.exe -m alembic upgrade head
    $env:PYTHONPATH='.'
    .\\.venv\\Scripts\\python.exe scripts/check_biz_doc_concurrency.py
    docker exec crm-postgres psql -U crm -d postgres -c "DROP DATABASE crm_iso_bd2;"

探针（对应 §8.9 的四条验收）：
1. 双击 / 响应丢失：**同一把请求键**两条连接同时提交 → 只有一份文件；
2. 两次明确新版：**不同请求键**并发 → 版本连续、parent 链正确、不报 500；
3. 同一来源不带键并发 → 也不产生同源同版本（唯一约束兜底 + 咨询锁排队）；
4. 不同来源并发 → 各自从 V1 起，**不共用历史链**；
5. 模板版本并发 → 两个管理员同时保存得到两个版本，而不是一个 500；
6. 唯一索引本身：手工插一条同源同版本必须被数据库拒绝。

跑完自己清掉本轮夹具与原件，不留脏数据。
"""

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402

PREFIX = f"CHKCC{int(time.time())}"
FAILURES: list[str] = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


async def _add_quote(tag: str, owner_id: int) -> tuple[int, int]:
    """再建一张干净的报价单（一个从未出过文件的新来源）。"""
    from app.modules.customer.model import Customer
    from app.modules.product.model import Product, Sku
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion

    async with SessionLocal() as s:
        customer = Customer(
            name=f"{PREFIX}客户{tag}", owner_id=owner_id, status="active", level="A"
        )
        s.add(customer)
        await s.flush()
        product = Product(name=f"{PREFIX}产品{tag}")
        s.add(product)
        await s.flush()
        sku = Sku(
            product_id=product.id,
            sku_code=f"{PREFIX}-{tag}-SKU",
            name=f"{PREFIX}法兰",
            unit="套",
            specification="DN50",
        )
        s.add(sku)
        await s.flush()
        quote = Quote(
            quote_no=f"{PREFIX}-Q{tag}",
            customer_id=customer.id,
            owner_id=owner_id,
            status="approved",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        s.add(quote)
        await s.flush()
        version = QuoteVersion(
            quote_id=quote.id,
            version_no=1,
            currency="CNY",
            payment_terms="30% 预付",
            delivery_terms="厂内交货",
            subtotal_amount=Decimal("100"),
            charge_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            total_amount=Decimal("100"),
            created_at=datetime.now(UTC),
        )
        s.add(version)
        await s.flush()
        quote.current_version_id = version.id
        s.add(
            QuoteItem(
                quote_version_id=version.id,
                sku_id=sku.id,
                sku_name_snapshot=sku.name,
                quantity=Decimal("2"),
                quoted_price=Decimal("50"),
                unit_snapshot="套",
            )
        )
        await s.commit()
        return quote.id, version.id


async def _gather_aligned(*coros, timeout=90):
    """把若干个协程对齐后同时开跑（用事件而不是 sleep：不靠时序碰运气）。

    每个协程自己开 session，因此**各自占用一条独立连接**——这正是复现并发
    问题需要的条件；共用一个 session 的话两条"并发"请求其实是串行的。
    """
    start = asyncio.Event()

    async def _wrapped(coro):
        await start.wait()
        return await coro

    tasks = [asyncio.create_task(_wrapped(coro)) for coro in coros]
    await asyncio.sleep(0)  # 让所有任务都跑到 start.wait()
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)


async def _seed():
    """造夹具：一个负责人、两个客户、两张报价单（各一版）。"""
    from app.modules.customer.model import Customer
    from app.modules.product.model import Product, Sku
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
    from app.modules.user.model import User

    async with SessionLocal() as s:
        owner = User(
            name=f"{PREFIX}负责人",
            username=f"{PREFIX.lower()}_u",
            password_hash="x",
            status="active",
        )
        s.add(owner)
        await s.flush()
        product = Product(name=f"{PREFIX}产品")
        s.add(product)
        await s.flush()
        sku = Sku(
            product_id=product.id,
            sku_code=f"{PREFIX}-SKU",
            name=f"{PREFIX}法兰",
            unit="套",
            specification="DN50",
        )
        s.add(sku)
        await s.flush()
        made = []
        for index in (1, 2):
            customer = Customer(
                name=f"{PREFIX}客户{index}",
                owner_id=owner.id,
                status="active",
                level="A",
            )
            s.add(customer)
            await s.flush()
            quote = Quote(
                quote_no=f"{PREFIX}-Q{index}",
                customer_id=customer.id,
                owner_id=owner.id,
                status="approved",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            s.add(quote)
            await s.flush()
            version = QuoteVersion(
                quote_id=quote.id,
                version_no=1,
                currency="CNY",
                payment_terms="30% 预付",
                delivery_terms="厂内交货",
                subtotal_amount=Decimal("100"),
                charge_amount=Decimal("0"),
                discount_amount=Decimal("0"),
                total_amount=Decimal("100"),
                created_at=datetime.now(UTC),
            )
            s.add(version)
            await s.flush()
            quote.current_version_id = version.id
            s.add(
                QuoteItem(
                    quote_version_id=version.id,
                    sku_id=sku.id,
                    sku_name_snapshot=sku.name,
                    quantity=Decimal("2"),
                    quoted_price=Decimal("50"),
                    unit_snapshot="套",
                )
            )
            made.append((quote.id, version.id))
        await s.commit()
        return owner.id, made


def _current_user(user_id: int):
    from app.core.deps import CurrentUser
    from app.modules.user.model import User
    from types import SimpleNamespace

    row = SimpleNamespace(
        id=user_id,
        name=f"{PREFIX}负责人",
        username=f"{PREFIX.lower()}_u",
        department_id=None,
    )
    _ = User
    return CurrentUser(row, permissions=set(), roles=[], data_scope="all")


async def _cleanup():
    """清掉本轮夹具与**盘上的原件**（库是一次性的，盘不是）。"""
    from sqlalchemy import text

    from app.modules.file import storage
    from app.modules.file.model import FileRecord

    async with SessionLocal() as s:
        keys = (
            await s.execute(
                text(
                    "select f.object_key from files f join biz_docs d on d.file_id = f.id "
                    "where d.customer_id in (select id from customers where name like :p)"
                ),
                {"p": f"{PREFIX}%"},
            )
        ).scalars().all()
        for sql in (
            "delete from request_keys where result_type = 'biz_doc' and result_id in "
            "(select id from biz_docs where customer_id in (select id from customers where name like :p))",
            "delete from biz_docs where customer_id in (select id from customers where name like :p)",
            "delete from biz_doc_templates where name like :p",
            "delete from quote_items where quote_version_id in "
            "(select id from quote_versions where quote_id in (select id from quotes where customer_id in "
            "(select id from customers where name like :p)))",
            "delete from quote_versions where quote_id in (select id from quotes where customer_id in "
            "(select id from customers where name like :p))",
            "delete from quotes where customer_id in (select id from customers where name like :p)",
            "delete from skus where sku_code like :p",
            "delete from products where name like :p",
            "delete from customers where name like :p",
            # 用户名是**小写**前缀（`chkcc..._u`），而 PREFIX 是 `CHKCC...`：
            # PostgreSQL 的 LIKE 区分大小写，用 :p 直接比会漏掉这一行，
            # 于是 check_fixture_residue 会在最后把它揪出来。两边都 lower 再比。
            "delete from users where lower(username) like lower(:p)",
        ):
            await s.execute(text(sql), {"p": f"{PREFIX}%"})
        await s.commit()
    _ = (FileRecord,)
    for key in keys:
        try:
            storage.delete_object(key)
        except Exception:  # noqa: BLE001 —— 清理不该顶掉结果
            pass


async def _count_docs(quote_id: int) -> int:
    from app.modules.bizdoc.model import BizDoc

    async with SessionLocal() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(BizDoc)
                .where(BizDoc.quote_id == quote_id)
            )
        ).scalar_one()


async def probe_same_request_key(version_id: int, user_id: int):
    """① 双击 / 响应丢失：同一把键并发 → 只有一份（验收：只一份）。"""
    print("=== 1. 同一把请求键、两条连接并发（双击 / 响应丢失）===")
    from app.modules.bizdoc import service as docs

    user = _current_user(user_id)
    payload = {"quote_version_id": version_id}
    action = "bizdoc:quote_sheet"

    async def _one():
        async with SessionLocal() as s:
            doc, replayed = await docs.run_idempotent_generation(
                s,
                user_id=user_id,
                action=action,
                request_key="cc-same-key",
                payload=payload,
                generate=lambda: docs.generate_quote_doc(
                    s, quote_version_id=version_id, user=user
                ),
            )
            doc_id, version = doc.id, doc.version
            await s.commit()
            return doc_id, version, replayed

    results = await _gather_aligned(_one(), _one())
    ids = {row[0] for row in results}
    check("两条并发请求指向同一份文件", len(ids), 1)
    check("版本号是 V1", {row[1] for row in results}, {1})
    check("第二次是回放，没有重复生成", sorted(row[2] for row in results), [False, True])
    return results


async def probe_two_new_versions(quote_id: int, version_id: int, user_id: int):
    """② 两次明确新版（不同键）并发 → 版本连续、链正确、不 500。

    这一条是 §8.9 的核心：**不能按内容相同去阻止合法新版**，也不能因为并发
    撞唯一约束就把第二个请求打成 500。两把不同的键 = 业务上明确要两份。
    """
    print("=== 2. 两把不同请求键并发（两次明确新版）===")
    from app.modules.bizdoc import service as docs
    from app.modules.bizdoc.model import BizDoc

    user = _current_user(user_id)
    async with SessionLocal() as s:
        before = (
            await s.execute(
                select(func.coalesce(func.max(BizDoc.version), 0)).where(
                    BizDoc.quote_id == quote_id
                )
            )
        ).scalar_one()

    async def _one(key: str):
        async with SessionLocal() as s:
            try:
                doc, _replayed = await docs.run_idempotent_generation(
                    s,
                    user_id=user_id,
                    action="bizdoc:quote_sheet",
                    request_key=key,
                    payload={"quote_version_id": version_id},
                    generate=lambda: docs.generate_quote_doc(
                        s, quote_version_id=version_id, user=user
                    ),
                )
                out = (doc.id, doc.version, doc.parent_id, doc.source_key)
                await s.commit()
                return ("ok", out)
            except Exception as exc:  # noqa: BLE001 —— 探针要看清"是不是 500"
                return ("error", f"{exc.__class__.__name__}: {exc}")

    results = await _gather_aligned(_one("cc-new-a"), _one("cc-new-b"))
    errors = [row for row in results if row[0] == "error"]
    check("两个请求都成功（没有唯一冲突 500）", errors, [])
    ok = [row[1] for row in results if row[0] == "ok"]
    # 期望的版本号按"并发之前这一来源已经出到第几版"推算：
    # 探针 1 已经出过一份，所以这里应当是 V2、V3 各一份（连续且不重号）
    expected = [before + 1, before + 2]
    check("拿到两个连续的、互不相同的版本号", sorted(row[1] for row in ok), expected)
    check("两条在同一来源链上", len({row[3] for row in ok}), 1)
    by_version = {row[1]: row for row in ok}
    check(f"V{expected[1]} 的 parent 指向 V{expected[0]}", by_version[expected[1]][2], by_version[expected[0]][0])
    check("库里正好三份（探针 1 一份 + 本轮两份）", await _count_docs(quote_id), 3)


async def probe_no_key_concurrent(quote_id: int, version_id: int, user_id: int):
    """③ 不带键并发 → 也不许出现同源同版本（唯一约束是最后一道闸）。"""
    print("=== 3. 不带请求键、两条连接并发（唯一约束兜底）===")
    from app.modules.bizdoc import service as docs

    user = _current_user(user_id)

    async def _one():
        async with SessionLocal() as s:
            try:
                doc = await docs.generate_quote_doc(
                    s, quote_version_id=version_id, user=user
                )
                out = ("ok", doc.version)
                await s.commit()
                return out
            except Exception as exc:  # noqa: BLE001
                await s.rollback()
                return ("error", f"{exc.__class__.__name__}: {exc}")

    results = await _gather_aligned(_one(), _one())
    versions = [row[1] for row in results if row[0] == "ok"]
    check_true("没有两个请求拿到同一个版本号", len(set(versions)) == len(versions), f"{results}")
    check("库里没有同源同版本", await _duplicate_rows(quote_id), 0)


async def _duplicate_rows(quote_id: int) -> int:
    """同源同版本的重复行数（验收原话：双连接并发不产生同源同版本）。"""
    from sqlalchemy import text

    async with SessionLocal() as s:
        return (
            await s.execute(
                text(
                    "select count(*) from (select doc_type, source_key, version "
                    "from biz_docs where quote_id = :qid and source_key is not null "
                    "group by 1,2,3 having count(*) > 1) dup"
                ),
                {"qid": quote_id},
            )
        ).scalar_one()


async def probe_different_sources(user_id: int):
    """④ 不同来源并发 → 各自从 V1 起（验收：不同来源不会共用历史链）。"""
    print("=== 4. 两个不同来源并发（不许共用历史链）===")
    from app.modules.bizdoc import service as docs

    user = _current_user(user_id)
    # 两个**全新**来源：谁都不该蹭到别人的版本号，两个都必须是 V1
    _q1, first = await _add_quote("S1", user_id)
    _q2, second = await _add_quote("S2", user_id)

    async def _one(version_id: int):
        async with SessionLocal() as s:
            doc = await docs.generate_quote_doc(
                s, quote_version_id=version_id, user=user
            )
            out = (doc.version, doc.parent_id, doc.source_key)
            await s.commit()
            return out

    results = await _gather_aligned(_one(first), _one(second))
    check("两个新来源都是 V1", sorted(row[0] for row in results), [1, 1])
    check(
        "都不是别人的续版（没有 parent）",
        [row[1] for row in results],
        [None, None],
    )
    check("来源键互不相同", len({row[2] for row in results}), 2)


async def probe_template_concurrency(user_id: int):
    """⑤ 模板版本并发（验收：有可解释结果，不是 500）。"""
    print("=== 5. 模板版本并发保存 ===")
    from app.modules.bizdoc import service as docs

    async def _one(name: str):
        async with SessionLocal() as s:
            try:
                row = await docs.create_template_version(
                    s,
                    doc_type="quote_sheet",
                    name=f"{PREFIX}{name}",
                    body="",
                    enabled=True,
                    remark=None,
                    user_id=user_id,
                )
                out = ("ok", row.version)
                await s.commit()
                return out
            except Exception as exc:  # noqa: BLE001
                await s.rollback()
                return ("error", f"{exc.__class__.__name__}: {exc}")

    results = await _gather_aligned(_one("模板A"), _one("模板B"))
    errors = [row for row in results if row[0] == "error"]
    check("两个管理员同时保存都成功", errors, [])
    versions = sorted(row[1] for row in results if row[0] == "ok")
    check("拿到两个不同版本号", len(set(versions)), 2)


async def probe_unique_index(version_id: int, user_id: int):
    """⑥ 唯一索引本身：手工插一条同源同版本，必须被数据库拒绝。"""
    print("=== 6. 同源同版本的唯一索引 ===")
    from app.modules.bizdoc import service as docs
    from app.modules.bizdoc.model import BizDoc

    user = _current_user(user_id)
    async with SessionLocal() as s:
        doc = await docs.generate_quote_doc(s, quote_version_id=version_id, user=user)
        await s.commit()
        source_key, existed_version = doc.source_key, doc.version
        clone = BizDoc(
            doc_no=f"{PREFIX}-CLONE",
            doc_type=doc.doc_type,
            title="同源同版本的重复行",
            version=existed_version,
            source_key=source_key,
            status="active",
            template_id=doc.template_id,
            template_version=doc.template_version,
            input_snapshot={},
            content_sha256="z" * 64,
            created_at=datetime.now(UTC),
        )
        s.add(clone)
        try:
            await s.flush()
            check_true("同源同版本被数据库拒绝", False, "居然插进去了")
        except IntegrityError:
            check_true("同源同版本被数据库拒绝", True, "uq_biz_docs_source_version")
        finally:
            await s.rollback()


async def probe_lock_removed_still_no_duplicate(user_id: int):
    """⑦ 把咨询锁摘掉：**修前的分配路径**仍然不能造出同源同版本。

    这一条是给"锁"和"唯一约束"分工留证据的：把 `_lock_sequence` 换成空操作
    （等价于修复前"读最新 +1、没有任何排他"的老实现），两条并发连接会双双
    读到同一个版本号，但**数据库唯一索引**会让其中一个明确失败（可解释的 409），
    而不是静默落两份 V(n)。修前正是"静默落两份"，所以这条同时说明：
    约束才是最后一道闸，锁只是让它不必失败。
    """
    print("=== 7. 摘掉咨询锁（模拟修前的分配路径）===")
    from unittest.mock import patch

    from app.core.errors import AppError
    from app.modules.bizdoc import service as docs

    _q, version_id = await _add_quote("NOLOCK", user_id)
    user = _current_user(user_id)

    async def _one():
        async with SessionLocal() as s:
            try:
                doc = await docs.generate_quote_doc(
                    s, quote_version_id=version_id, user=user
                )
                out = ("ok", doc.version)
                await s.commit()
                return out
            except AppError as exc:
                await s.rollback()
                return ("app_error", exc.http_status)
            except Exception as exc:  # noqa: BLE001
                await s.rollback()
                return ("error", f"{exc.__class__.__name__}")

    async def _no_lock(_session, _key):
        return False

    with patch.object(docs, "_lock_sequence", _no_lock):
        results = await _gather_aligned(_one(), _one())

    ok_versions = [row[1] for row in results if row[0] == "ok"]
    app_errors = [row[1] for row in results if row[0] == "app_error"]
    check_true(
        "没有出现「两份同版本」",
        len(set(ok_versions)) == len(ok_versions),
        f"{results}",
    )
    check_true(
        "失败的那一条是可解释的 409（不是 500、也不是静默重复）",
        app_errors == [409],
        f"{results}",
    )
    check("库里没有同源同版本", await _duplicate_rows_by_version(version_id), 0)


async def _duplicate_rows_by_version(version_id: int) -> int:
    from sqlalchemy import text

    async with SessionLocal() as s:
        return (
            await s.execute(
                text(
                    "select count(*) from (select doc_type, source_key, version from biz_docs "
                    "where quote_id = (select quote_id from quote_versions where id = :vid) "
                    "and source_key is not null group by 1,2,3 having count(*) > 1) dup"
                ),
                {"vid": version_id},
            )
        ).scalar_one()


async def main():
    parsed = urlparse(settings.database_url)
    check_true(
        "只允许跑在一次性隔离库上",
        "iso" in parsed.path.lower() or "check" in parsed.path.lower(),
        f"{settings.database_url}",
    )
    check_true(
        "只允许连本机数据库",
        parsed.hostname in {"127.0.0.1", "localhost", "::1"},
        f"{parsed.hostname}",
    )
    check_true("不进行任何真实外部推送", settings.dingtalk_push_off and settings.wecom_push_off)
    if FAILURES:
        print("前置条件不满足，已退出")
        return 1

    print(f"file_root = {settings.file_root}")
    user_id, versions = await _seed()
    quote_id, version_id = versions[0]
    try:
        await probe_same_request_key(version_id, user_id)
        await probe_two_new_versions(quote_id, version_id, user_id)
        await probe_no_key_concurrent(quote_id, version_id, user_id)
        await probe_different_sources(user_id)
        await probe_template_concurrency(user_id)
        await probe_unique_index(version_id, user_id)
        await probe_lock_removed_still_no_duplicate(user_id)
    finally:
        await _cleanup()

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("§8.9 真库并发验证 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
