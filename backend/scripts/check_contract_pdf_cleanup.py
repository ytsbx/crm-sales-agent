#!/usr/bin/env python3
"""第四批复审追加：**合同 PDF 清理的两条失败路径**（真库 + 真存储目录故障注入）。

复审指出我上一版有两个问题，这份脚本逐个验：

  **① 漏清理**：`try` 只包住了 `session.commit()`，所以"PDF 已落盘、写审计失败"
     这条路没有清理 —— 数据库回滚（合同 0 条、文件记录 0 条），盘上 PDF 留着。

  **② 误删原件**（更严重，我引入的）：`commit()` 抛异常 ≠ 没提交。
     提交成功但回执丢失时，数据库里合同与文件记录**都在**，异常照抛，
     而我上一版在 `except` 里**无条件删除** → 把已经入库的原件删掉了。

修法：`try` 从落盘之前开始覆盖每一步；删除前**先回滚再核查 `file_records`**，
查得到就保留。

## 怎么注入

不 mock 业务逻辑，而是**替换一个必然会被调用到的下游动作**：

- 路径 A（漏清理）：让 `write_audit` 抛错 —— 它在落盘之后、提交之前；
- 路径 B（误删）：让 `session.commit` **先真的提交、再抛错**，模拟"回执丢失"。

## 跑法

    DB=crm_check_r6
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL FILE_ROOT=data/iso-files-r6 \\
      .venv/bin/python scripts/check_contract_pdf_cleanup.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
import sys
import uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_isolated_db  # noqa: E402

RUN = 'R6PDF'
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


def _register_all_models() -> None:
    import importlib
    import pkgutil

    import app.modules as modules

    for info in pkgutil.iter_modules(modules.__path__):
        try:
            importlib.import_module(f'app.modules.{info.name}.model')
        except ModuleNotFoundError:
            continue


_register_all_models()


class _User:
    """够用的 CurrentUser 替身（只用到 id）。"""

    id = 1
    name = '故障注入'
    username = 'admin'
    role_codes: list[str] = ['admin']
    data_scope = 'all'
    permission_codes: list[str] = []


class _Request:
    """够用的 Request 替身（只用到 client/headers 取 IP）。"""

    class client:  # noqa: N801
        host = '127.0.0.1'

    headers: dict[str, str] = {}


async def make_customer_and_template() -> tuple[int, int]:
    """造客户与模板（**不造合同**）。

    ⚠️ 合同草稿必须在**与端点调用同一个事务**里创建，否则验证不了回滚：
    第一版我在这里就 `commit()` 了那份草稿，于是"写审计失败"那一路查出来
    "合同 1 条" —— 那 1 条正是夹具自己提交的，跟被测事务无关（假红）。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        customer_id = (await session.execute(text(
            "insert into customers (name, owner_id, status, pool_status, created_at, updated_at)"
            " values (:n, 1, 'active', 'private', now(), now()) returning id"
        ), {'n': f'{RUN}客户-{uuid.uuid4().hex[:6]}'})).scalar_one()
        template_id = (await session.execute(text(
            "insert into contract_templates (doc_type, name, body, version, enabled,"
            " created_at) values ('contract', :n, :b, 1, true, now()) returning id"
        ), {'n': f'{RUN}模板-{uuid.uuid4().hex[:6]}',
            'b': '甲方：{{customer_name}}\\n金额：{{total_amount}}'})).scalar_one()
        await session.commit()
        return int(customer_id), int(template_id)


def _pending_count() -> int:
    """本进程里还有几个会话挂着"待清理"登记。

    ⚠️ 复审那条反例的第二半就是这个：取消之后**登记仍留着 1 条**。
    留着比孤儿 PDF 更麻烦 —— 那个会话之后再走清理，会把早已没人管的键当待删对象。
    """
    from app.modules.contract import service as contract_service

    return sum(len(keys) for keys in contract_service._PENDING_OBJECTS.values())


def storage_files() -> list[str]:
    """存储根目录下现在有哪些文件（用来判断 PDF 有没有被清掉/误删）。"""
    from app.modules.file.storage import file_root

    root = file_root()
    return sorted(str(p.relative_to(root)) for p in root.rglob('*.pdf'))


async def object_key_in_db(object_key: str) -> bool:
    """这个 object_key 有没有对应的 `files` 记录。

    ⚠️ 断言必须按 **object_key** 查，不能按文件名 —— 文件名是 `doc_no.pdf`
    （如 `CT202610120006.pdf`），跟我的测试前缀 `R6PDF` 毫无关系。
    我第一版按 `file_name like 'R6PDF%'` 数，永远数到 0，
    于是把"保留成功"误判成"文件记录不在"（假红，查了三次库才发现）。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        return (
            await session.execute(text(
                'select count(*) from files where object_key = :k'
            ), {'k': object_key})
        ).scalar_one() > 0


async def count_contract_rows(doc_id: int | None) -> tuple[int, int]:
    """(**这份合同是否存在**, 它的文件记录数) —— 判断事务到底有没有提交。

    ⚠️ 第一版按 `doc_no like 'R6PDF%'` 数，把**夹具预建的那条草稿**也数进去了，
    于是"写审计失败"那一路显示"合同 1 条"（其实是夹具），看着像没回滚。
    必须按 `doc_id` 数 —— 夹具和被测对象要能分开。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        docs = (await session.execute(text(
            'select count(*) from contract_documents where id = :i'
        ), {'i': doc_id})).scalar_one()
        files = (await session.execute(text(
            'select count(*) from files f join business_files bf on bf.file_id = f.id '
            " where bf.business_type = 'contract' and bf.business_id = :i"
        ), {'i': doc_id})).scalar_one()
        return int(docs), int(files)


async def run_endpoint(customer_id: int, template_id: int, *, fail_audit: bool,
                       commit_then_raise: bool, cancel_at_audit: bool = False):
    """直接调**真实端点函数**（不经过 HTTP 层，但业务逻辑一模一样）。"""
    from app.core.database import SessionLocal
    from app.modules.contract import router as contract_router
    from app.modules.contract.model import ContractDocument

    original_write_audit = contract_router.write_audit
    doc_id_holder: list[int] = [0]

    async def boom_write_audit(*args, **kwargs):
        raise RuntimeError('故障注入：写审计失败')

    async def cancel_write_audit(*args, **kwargs):
        # 模拟"协程被取消"：客户端断开 / 请求超时 / 优雅停机。
        # ⚠️ 抛的是 `CancelledError`，它继承 `BaseException` —— 这正是复审那条
        # 反例的关键：`except Exception` 捕不到它。
        raise asyncio.CancelledError('故障注入：写审计时被取消')

    try:
        if fail_audit:
            contract_router.write_audit = boom_write_audit
        if cancel_at_audit:
            contract_router.write_audit = cancel_write_audit

        async with SessionLocal() as session:
            # 草稿在**这个会话**里创建：它和端点后续的写入同属一个事务，
            # 端点的 rollback 才能把它一起回滚掉
            doc = ContractDocument(
                doc_no=f'{RUN}-{uuid.uuid4().hex[:6]}',
                doc_type='contract',
                title=f'{RUN}合同',
                customer_id=customer_id,
                template_id=template_id,
                status='draft',
                content_snapshot='',  # Text：草稿正文（模型里不是 JSON）
                filled_data={},
                created_at=datetime.now(UTC),  # 这一列没有默认值（必填）
            )
            session.add(doc)
            await session.flush()
            doc_id = doc.id
            doc_id_holder[0] = int(doc.id)

            if commit_then_raise:
                # 模拟"提交成功、回执丢失"：先真提交，再抛错
                real_commit = session.commit

                async def commit_then_boom():
                    await real_commit()
                    # 诊断：提交之后立刻用**另一条连接**查一次，看数据到底在不在
                    from sqlalchemy import text as _text

                    from app.core.database import SessionLocal as _SL

                    async with _SL() as probe:
                        docs = (await probe.execute(_text(
                            'select count(*) from contract_documents where id = :i'
                        ), {'i': doc_id_holder[0]})).scalar_one()
                    # 这条是"提交其实成功了"的**直接证据**：提交之后另开连接能查到
                    print(f'  · [证据] 提交后另开连接查：合同 {docs} 条（>0 即已落库）')
                    raise RuntimeError('故障注入：提交回执丢失')

                session.commit = commit_then_boom  # type: ignore[method-assign]

            await contract_router.create_document_record(
                payload=make_payload(template_id, customer_id),
                request=_Request(),
                user=_User(),
                session=session,
            )
            return doc_id
    finally:
        contract_router.write_audit = original_write_audit


def make_payload(template_id: int, customer_id: int):
    """用**真实的 `DocumentCreate`**，不手搓替身。

    第一版我写了个只有属性的桩，结果端点在函数体里调 `payload.model_dump()`，
    桩没有这个方法 —— 两个路径都在**到达被测逻辑之前**就抛了 AttributeError，
    于是"通过"的那几条其实是假绿（盘上没 PDF 是因为压根没跑到落盘那一步）。
    """
    from app.modules.contract.schema import DocumentCreate

    return DocumentCreate(
        template_id=template_id,
        customer_id=customer_id,
        request_key=str(uuid.uuid4()),
    )


async def cleanup() -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal
    from app.modules.file.storage import file_root

    async with SessionLocal() as session:
        await session.execute(text(
            "delete from business_files where business_type = 'contract' and business_id in "
            '(select id from contract_documents where doc_no like :p)'
        ), {'p': f'{RUN}%'})
        # ⚠️ 顺序：**合同先删，模板后删** —— 模板被 `template_id` 外键引用着
        #（实测撞过 contract_documents_template_id_fkey）
        # 按"引用了本次造的模板"来删，而不是只按单号前缀 ——
        # 上一次跑到一半失败会留下单号不带随机后缀的合同（实测残留过一条）
        await session.execute(text(
            'delete from contract_documents where doc_no like :p '
            '   or template_id in (select id from contract_templates where name like :p)'
        ), {'p': f'{RUN}%'})
        await session.execute(text(
            "delete from contract_templates where name like :p"
        ), {'p': f'{RUN}%'})
        # files 的名字是 `doc_no.pdf`，不带测试前缀 —— 按 object_key 清
        # （object_key 形如 `2026/10/xxx.pdf`），靠"本次测试运行时记录的键"更准；
        # 这里退一步：删掉所有**没有 business_files 关联**的孤儿文件记录
        await session.execute(text(
            'delete from files where id in ('
            '  select f.id from files f left join business_files bf on bf.file_id = f.id '
            '  where bf.id is null)'
        ))
        await session.execute(text(
            'delete from customers where name like :p'
        ), {'p': f'{RUN}%'})
        await session.commit()
    # 盘上的残留（测试自己造的）也清掉
    root = file_root()
    for path in root.rglob('*.pdf'):
        path.unlink(missing_ok=True)
    print('  （夹具与临时 PDF 已清）')


async def main() -> None:
    require_isolated_db()
    await cleanup()

    # ---------------------------------------------------------------- 路径 A
    print()
    print('=== A. 写审计失败（落盘之后、提交之前）—— 必须清掉 PDF ===')
    customer_a, template_a = await make_customer_and_template()
    before = storage_files()
    doc_a = None
    try:
        doc_a = await run_endpoint(customer_a, template_a,
                                   fail_audit=True, commit_then_raise=False)
    except Exception as exc:  # noqa: BLE001 —— 期望就是抛
        print(f'  · 端点如预期抛出：{type(exc).__name__}: {exc}')
    docs, files = await count_contract_rows(doc_a)
    after = storage_files()
    new_pdfs = [p for p in after if p not in before]
    check('数据库里没有合同记录（事务确实回滚了）', docs, 0)
    check('数据库里没有文件记录', files, 0)
    check_true('盘上**没有**留下孤儿 PDF', new_pdfs == [], f'新增={new_pdfs}')

    # ---------------------------------------------------------------- 路径 B
    print()
    print('=== B. 提交成功但回执丢失 —— **不许删原件**（我上一版就错在这里）===')
    customer_b, template_b = await make_customer_and_template()
    before_b = storage_files()
    try:
        await run_endpoint(customer_b, template_b,
                           fail_audit=False, commit_then_raise=True)
    except Exception as exc:  # noqa: BLE001 —— 期望就是抛
        print(f'  · 端点如预期抛出：{type(exc).__name__}: {exc}')
    after_b = storage_files()
    new_pdfs_b = [p for p in after_b if p not in before_b]
    # 用"盘上那个文件"反查它的 object_key，再问库里有没有它 —— 这正是清理逻辑的判据
    kept_in_db = [await object_key_in_db(p) for p in new_pdfs_b]
    print(f'  · 盘上新增 PDF {len(new_pdfs_b)} 个；它们在库里的登记情况 {kept_in_db}')
    # ⚠️ 断言只看**这个 PDF 有没有被保留**，不去数合同行：
    # 合同行数受"最后一次 cleanup 会按模板关联把它删掉"影响（夹具之间共享模板），
    # 数它得到的是清理的效果，不是被测逻辑的效果（我在这上面来回错了三次）。
    # "提交其实成功了"这件事由上面的诊断探针直接证明（提交后另开连接查到合同 1 条）。
    check_true('PDF **保留**（已入库的原件不能删）', len(new_pdfs_b) == 1,
               f'新增={new_pdfs_b}')
    check_true('而且它对应的 `files` 记录**在**（说明删除确实被拦住了）',
               kept_in_db == [True], f'{kept_in_db}')

    print()
    # ---------------------------------------------------------------- 路径 C
    print()
    print('=== C. PDF 落盘后被**取消**（CancelledError）—— 必须清干净（第四批复审 P2）===')
    print('  · 关键：`CancelledError` 继承 `BaseException`，`except Exception` 捕不到它')
    customer_c, template_c = await make_customer_and_template()
    before_c = storage_files()
    try:
        await run_endpoint(customer_c, template_c, fail_audit=False,
                           commit_then_raise=False, cancel_at_audit=True)
    except asyncio.CancelledError as exc:
        print(f'  · 端点如预期抛出取消：{exc}')
    after_c = storage_files()
    new_pdfs_c = [p for p in after_c if p not in before_c]
    check_true('盘上**没有**留下孤儿 PDF', new_pdfs_c == [], f'新增={new_pdfs_c}')
    pending_left = _pending_count()
    check_true('待清理登记**已注销**（不许留在会话上）', pending_left == 0, f'残留={pending_left}')

    print()
    print('=== 收尾 ===')
    await cleanup()


if __name__ == '__main__':
    asyncio.run(main())
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
