"""第四批审查 6 项（3 个 P1、3 个 P2）的离线回归。

每一条都对应一处**已经实测复现过**的缺陷，所以断言写的是"修好之后必须成立的事"，
而不是"实现细节长什么样"：

1. 账期识别（`payment_terms_days`）—— 见 `test_approval_rules_engine.py`；
2. 外币不让它参与人民币口径的计算（`reject_foreign_currency`）；
3. 实收日期不能在未来（`assert_received_date_not_future`）；
4. CSV 导出公式注入；
5. CSV 多余列不再 500；
6. 合同 PDF 在失败路径上要清掉。
"""

import pathlib
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.csvio import csv_bytes, parse_csv_bytes
from app.core.errors import AppError, ErrorCode
from app.core.trade_mode import (
    CNY,
    FOREIGN_NOT_SUPPORTED,
    is_foreign,
    reject_foreign_currency,
)
from app.modules.payment.service import assert_received_date_not_future


# ------------------------------------------------- 2. 外币：不做折算，直接拒


class TestForeignCurrencyRejected:
    """主人 2026-10-12 的口径：**只做国内**，外币不做折算、见到就报错。

    重点不是"报错好看"，而是**外币金额绝不能参与人民币口径的计算**：
    `concession_amount` 的单位是「元」，拿外币差额当人民币匹配阈值，
    会让本该人工审的单子走极速通道（第四批审查 P1）。
    """

    def test_cny_passes(self):
        reject_foreign_currency(CNY, None, "", label="总额")  # 不抛就是通过

    def test_foreign_raises(self):
        for code in ("USD", "usd", " EUR ", "JPY"):
            with pytest.raises(AppError) as exc:
                reject_foreign_currency(code, label="报价版本总额")
            assert exc.value.code == ErrorCode.STATUS_NOT_ALLOWED
            assert "只支持人民币" in exc.value.message
            assert code.strip().upper() in exc.value.message  # 说清是哪个币种

    def test_message_tells_what_to_do(self):
        """报错必须给出**走得通**的出路。

        ⚠️ 这条断言原来还要求出现「业务口径」—— 那句是
        "先请管理员把业务口径改成「国内与出口都做」"。币种改成固定白名单之后，
        **改配置也不放行**了，那句话等于把用户指到一条走不通的路上
        （复审实测在核价入口看到的正是这句）。所以现在反过来断言：
        提示要**明确说清"这不是改配置能放开的"**，而不是让用户白跑一趟。
        """
        assert "改回人民币" in FOREIGN_NOT_SUPPORTED
        assert "业务口径" not in FOREIGN_NOT_SUPPORTED, "别再引导用户去改口径（改了也没用）"
        assert "改配置" in FOREIGN_NOT_SUPPORTED, "要说清这不是配置问题"

    def test_previous_version_currency_also_checked(self):
        """两个版本的币种**都要查**：只查当前版会漏掉"新版人民币、旧版美元"。"""
        with pytest.raises(AppError):
            reject_foreign_currency(CNY, "USD", label="报价版本总额")

    def test_is_foreign_helper(self):
        assert is_foreign(CNY, None, "") is False
        assert is_foreign(CNY, "USD") is True


# --------------------------------------------- 3. 实收日期不能在未来


class TestReceivedDateNotFuture:
    """第四批审查 P1：今天 10/12、到账日填 10/22，走完登记+确认后
    回款变 `confirmed`、应收变 `paid`、跟单节点标完成 —— 钱没到就算收齐，
    而且按确认时间统计的本月回款会提前计入。
    """

    def test_today_is_ok(self):
        from app.core.timebase import today_business

        assert_received_date_not_future(today_business())

    def test_past_is_ok(self):
        from app.core.timebase import today_business

        assert_received_date_not_future(today_business() - timedelta(days=30))

    def test_none_is_ok(self):
        """没给日期不是这条判据管的事（必填是别的判据）。"""
        assert_received_date_not_future(None)

    def test_future_raises(self):
        from app.core.timebase import today_business

        future = today_business() + timedelta(days=10)
        with pytest.raises(AppError) as exc:
            assert_received_date_not_future(future, action="确认回款")
        assert exc.value.code == ErrorCode.PARAM_ERROR
        # 报错要说清"是哪天、今天是哪天、该改去哪儿"
        assert future.strftime("%Y-%m-%d") in exc.value.message
        assert today_business().strftime("%Y-%m-%d") in exc.value.message
        assert "应收计划" in exc.value.message
        assert "确认回款" in exc.value.message

    def test_boundary_tomorrow_raises(self, ):
        """差一天也算未来 —— 边界不能松。"""
        from app.core.timebase import today_business

        with pytest.raises(AppError):
            assert_received_date_not_future(today_business() + timedelta(days=1))


# --------------------------------------------- 4. CSV 导出：公式注入


class TestCsvFormulaInjection:
    """第四批审查 P2：客户名 `=1+1` 导出后仍是 `=1+1`，Excel 会当公式解析。"""

    def _decode(self, raw: bytes) -> str:
        return raw.decode("utf-8-sig")

    def test_formula_chars_are_neutralised(self):
        raw = csv_bytes(
            [["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "正常客户"]],
            ["客户名称", "A", "B", "C", "D"],
        )
        line = self._decode(raw).splitlines()[1]
        for cell in line.split(",")[:4]:
            assert cell.startswith("'"), f"公式起始字符没被保护：{cell}"
        assert line.rstrip().endswith("正常客户")

    def test_normal_text_untouched(self):
        raw = csv_bytes([["宁波宏远包装", "备注里有个=号"]], ["名称", "备注"])
        line = self._decode(raw).splitlines()[1]
        assert line.startswith("宁波宏远包装")
        # `=` 不在开头就不该动它（别把正常内容改掉）
        assert "'" not in line.split(",")[0]

    def test_numbers_keep_numeric_semantics(self):
        """数值列**必须原样是数字**：被转成字符串后排序/求和/透视表全废。"""
        raw = csv_bytes([[123, Decimal("45.60"), 1.5]], ["数量", "金额", "比率"])
        line = self._decode(raw).splitlines()[1]
        assert line == "123,45.60,1.5"

    def test_header_is_protected_too(self):
        raw = csv_bytes([["值"]], ["=A1"])
        assert self._decode(raw).splitlines()[0].startswith("'=A1")


# --------------------------------------------- 5. CSV 导入：多余列不再 500


class TestCsvExtraColumns:
    """第四批审查 P2：行比表头列多时 `DictReader` 把多余值放进**列表**，
    空行过滤对它调 `.strip()` → `AttributeError` → 整份文件 500。
    """

    def test_reported_case_gives_business_error(self):
        with pytest.raises(AppError) as exc:
            parse_csv_bytes(
                "客户名称\n,多余值\n".encode(), required_headers=["客户名称"]
            )
        assert exc.value.code == ErrorCode.PARAM_ERROR
        assert "第 2 行" in exc.value.message
        assert "多余值" in exc.value.message

    def test_normal_row_before_bad_row_still_reports_line_number(self):
        """正常行夹着异常行：要报**异常那一行**的行号。"""
        text = "客户名称\n正常客户\n,多余值\n另一个正常客户\n"
        with pytest.raises(AppError) as exc:
            parse_csv_bytes(text.encode(), required_headers=["客户名称"])
        assert "第 3 行" in exc.value.message

    def test_extra_column_is_never_silently_dropped(self):
        """**不能静默丢掉**多余列 —— 丢掉会让填错列的人以为导入成功了。

        `,多余值` 这一行表头列恰好是空的，很容易被"空行"判断吃掉
        （我第一版就这么写错了），所以单列一条钉住。
        """
        with pytest.raises(AppError):
            parse_csv_bytes(",只有多余列\n".encode(), required_headers=["客户名称"])

    def test_blank_rows_are_skipped(self):
        rows = parse_csv_bytes(
            "客户名称\n\n正常客户\n,\n".encode(), required_headers=["客户名称"]
        )
        assert rows == [{"客户名称": "正常客户"}]

    def test_normal_file_unaffected(self):
        rows = parse_csv_bytes(
            "客户名称,备注\n甲,好客户\n乙,\n".encode(),
            required_headers=["客户名称"],
        )
        assert rows == [{"客户名称": "甲", "备注": "好客户"}, {"客户名称": "乙", "备注": ""}]

    def test_missing_header_still_reported(self):
        with pytest.raises(AppError) as exc:
            parse_csv_bytes("别的列\n甲\n".encode(), required_headers=["客户名称"])
        assert "表头缺少" in exc.value.message


# --------------------------------------------- 6. 合同 PDF：失败要清理


class TestContractPdfCleanup:
    """第四批审查 P2：PDF 先落盘、后登记文件，中间或外层失败时没有删除补偿
    （实测"数据库 0 条、存储里 PDF 仍在、删除补偿 0 次"）。
    """

    @pytest.mark.anyio
    async def test_pending_object_registered_and_taken(self):
        """落盘即登记；提交成功后取走登记（不留下"待删"的错觉）。"""
        from app.modules.contract import service as contract_service

        class _FakeSession:
            pass

        session = _FakeSession()
        contract_service.remember_pending_object(session, "contracts/a.pdf")
        contract_service.remember_pending_object(session, "contracts/b.pdf")
        assert contract_service.take_pending_objects(session) == [
            "contracts/a.pdf",
            "contracts/b.pdf",
        ]
        # 取走即清空：不能重复删
        assert contract_service.take_pending_objects(session) == []

    @pytest.mark.anyio
    async def test_lost_commit_ack_must_not_delete_the_pdf(self):
        """**第四批复审 P1（我引入的）**：`commit()` 抛异常 ≠ 事务没提交。

        提交成功但回执丢了（网络断）时，数据库里合同与文件记录**都在**，
        异常却照抛。我上一版在 except 里无条件 `delete_object`，
        于是把**已经入库的原件**删掉了：记录 1 条、下载 404 ——
        比"留个孤儿 PDF"严重得多。

        修好之后：删之前必须核查 `file_records`；查得到就**保留**。
        """

        from app.modules.contract import service as contract_service

        calls: list[str] = []

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return 1  # 假装"这个 object_key 已经在库里了"

        class _FakeSession:
            async def rollback(self):
                calls.append('rollback')

            async def execute(self, *_a, **_kw):
                calls.append('select')
                return _Result()

        session = _FakeSession()
        contract_service.remember_pending_object(session, 'contracts/committed.pdf')

        # 把 storage.delete_object 换成记录器（不该被调用）
        from app.modules.file import storage

        original = storage.delete_object
        storage.delete_object = lambda key: calls.append(f'delete:{key}')
        try:
            deleted = await contract_service.cleanup_pending_objects(session)
        finally:
            storage.delete_object = original

        assert deleted == 0, '已入库的对象不许删'
        assert not any(c.startswith('delete:') for c in calls), f'不该有删除调用：{calls}'
        assert 'rollback' in calls and 'select' in calls, '必须先回滚再核查'
        # 登记也要清掉（成功、失败都不能留）
        assert contract_service.take_pending_objects(session) == []

    @pytest.mark.anyio
    async def test_uncommitted_object_is_deleted(self):
        """反过来：确实没提交（库里查不到）才删 —— 别修成"永远不删"。"""
        from app.modules.contract import service as contract_service
        from app.modules.file import storage

        calls: list[str] = []

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return None  # 库里没有

        class _FakeSession:
            async def rollback(self):
                calls.append('rollback')

            async def execute(self, *_a, **_kw):
                return _Result()

        session = _FakeSession()
        contract_service.remember_pending_object(session, 'contracts/orphan.pdf')
        original = storage.delete_object
        storage.delete_object = lambda key: calls.append(f'delete:{key}')
        try:
            deleted = await contract_service.cleanup_pending_objects(session)
        finally:
            storage.delete_object = original

        assert deleted == 1
        assert 'delete:contracts/orphan.pdf' in calls

    @pytest.mark.anyio
    async def test_verify_failure_keeps_the_object(self):
        """核查本身失败时**不删**：宁可留孤儿，不能删原件。"""
        from app.modules.contract import service as contract_service
        from app.modules.file import storage

        class _FakeSession:
            async def rollback(self):
                pass

            async def execute(self, *_a, **_kw):
                raise RuntimeError('数据库连不上')

        session = _FakeSession()
        contract_service.remember_pending_object(session, 'contracts/unknown.pdf')
        original = storage.delete_object
        called: list[str] = []
        storage.delete_object = lambda key: called.append(key)
        try:
            deleted = await contract_service.cleanup_pending_objects(session)
        finally:
            storage.delete_object = original

        assert deleted == 0
        assert called == [], '核查不了就不许删'

    @pytest.mark.anyio
    async def test_take_is_per_session(self):
        """按会话隔离：并发请求各删各的，不能互相误删。"""
        from app.modules.contract import service as contract_service

        class _S:
            pass

        a, b = _S(), _S()
        contract_service.remember_pending_object(a, "contracts/from-a.pdf")
        contract_service.remember_pending_object(b, "contracts/from-b.pdf")
        assert contract_service.take_pending_objects(a) == ["contracts/from-a.pdf"]
        assert contract_service.take_pending_objects(b) == ["contracts/from-b.pdf"]


# ------------------------------------- 复审追加：人民币白名单（不看口径开关）


class TestCurrencyIsFixedWhitelist:
    """第四批复审指出：`ensure_currency_allowed` 在 `export` 口径下仍放行 USD。

    主人的口径是**没有外币这个东西**，所以不允许存在"某个开关一开外币就进来"的
    路径 —— 那不是"配置"，那是口子。
    """

    def test_cny_and_empty_pass(self):
        import asyncio

        from app.core.trade_mode import ensure_currency_allowed

        async def run():
            for code in ('CNY', 'cny', ' CNY ', None, ''):
                assert await ensure_currency_allowed(None, code) == CNY

        asyncio.run(run())

    def test_foreign_rejected_regardless_of_trade_mode(self):
        """**关键**：口径配成 export / both 也一律拒。session 直接传 None ——
        新实现压根不读它（读了才会有"看开关放行"的口子）。"""
        import asyncio

        from app.core.errors import AppError, ErrorCode
        from app.core.trade_mode import ensure_currency_allowed

        async def run():
            for code in ('USD', 'usd', 'EUR', 'HKD'):
                try:
                    await ensure_currency_allowed(None, code)
                except AppError as exc:
                    assert exc.code == ErrorCode.STATUS_NOT_ALLOWED
                    assert '人民币' in exc.message
                else:
                    raise AssertionError(f'{code} 被放行了 —— 白名单没生效')

        asyncio.run(run())

    def test_gate_no_longer_reads_trade_mode(self):
        """源码断言：这道闸里**不许**再出现按口径开关分支的写法。

        做法上钉住比行为断言更稳：行为测试只能证明"我试的这几个币种被拒了"，
        钉不住"将来有人把 `if export: return code` 加回来"。
        """

        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / 'app' / 'core' / 'trade_mode.py').read_text(encoding='utf-8')
        start = src.index('async def ensure_currency_allowed(')
        nl = chr(10)
        block = src[start : src.index(nl + 'def ', start + 10)]
        assert 'is_domestic_only' not in block, '这道闸不能再按口径开关放行外币'
