"""第五批审查 5 项（2 个 P1、3 个 P2）的离线回归。

| # | 项 | 这条测试钉住的事 |
| --- | --- | --- |
| P1-1 | 并发普通结账覆盖已冻结结果 | 结账入口**先取期间锁再查是否已结账**（顺序不能反） |
| P1-2 | 结账冻住不一致的汇总/明细 | 取数前设**一致读取快照**（REPEATABLE READ），两条查询看同一份数据 |
| P2-3 | 任务编辑收版本号却不校验 | PATCH 用 `expected_updated_at` 判冲突；且它**不被写进库** |
| P2-4 | 单件体积提前舍入 → 整批运费 0 | 单件体积是**未舍入的中间量**；长宽高与箱规两条路径都要 |
| P2-5 | 通用费率抢占专属费率 | 同承运商同运输方式内，专属压过通用；跨承运商仍比价 |
"""

from decimal import Decimal

from app.modules.pricing.logistics import (
    _prefer_specific,
    _volume_from_dimensions,
    rate_specificity,
)


# ------------------------------------------------- P1-1 / P1-2：结账的两处保护


class TestFreezeGuards:
    """这两条是"接线"性质的防护，用源码断言 + 行为断言两层钉住。

    为什么源码也要断言：`begin_consistent_read` 必须排在**第一条读库语句之前**
    （`SET TRANSACTION` 的硬要求），而"它在第几行"这件事行为测试看不出来 ——
    第二批复审那次就是靠源码断言抓到"函数只是定义了、没被调用"。
    """

    def _source(self) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        return (root / 'app' / 'modules' / 'analytics' / 'target_actuals.py').read_text(
            encoding='utf-8'
        )

    def _router_source(self) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        return (root / 'app' / 'modules' / 'analytics' / 'router.py').read_text(
            encoding='utf-8'
        )

    def test_lock_is_taken_before_checking_frozen(self):
        """**先锁、再查** —— 顺序反了就等于没锁（两个人可以双双越过检查）。"""
        body = self._source()
        start = body.index('async def freeze_period(')
        block = body[start:]
        lock_at = block.index('lock_period(session, period)')
        check_at = block.index('existing = await frozen_for(session, period)')
        assert lock_at < check_at, '必须先在 lock_period 里取锁，再查是否已结账'

    def test_consistent_read_is_used_before_collecting(self):
        """取数必须走**同一个一致快照**（汇总与明细是两条查询）。

        ⚠️ 我第一版是在请求会话上执行 `SET TRANSACTION ... REPEATABLE READ`，
        结果接口直接 500：`SET TRANSACTION must be called before any query` ——
        请求会话在进入处理函数前就已经查过库了（依赖注入要加载当前用户）。
        所以现在改用**独立连接**（`consistent_snapshot`），它的第一句就是这个 SET。
        这条断言钉住"取数在快照里"，而不是钉某个具体写法。
        """
        router = self._router_source()
        # 端点叫 refreeze（重算）—— 别按 recalc 去猜名字，猜错这断言会永远绿
        for endpoint in ('freeze_sales_actuals', 'refreeze_sales_actuals'):
            start = router.index(f'async def {endpoint}(')
            block = router[start:]
            snap_at = block.index('consistent_snapshot(period)')
            collect_at = block.index('_collect_period_actuals(snap, user, period)')
            items_at = block.index('_collect_period_items(snap, user, period)')
            assert snap_at < collect_at < items_at, (
                f'{endpoint}：汇总与明细必须都在同一个快照会话里读'
            )

    def test_snapshot_helper_sets_isolation_and_lock_together(self):
        """隔离级别与期间锁必须在**同一条连接**上：锁的作用域是事务，换连接锁就没了。"""
        src = self._source()
        start = src.index('async def consistent_snapshot(')
        block = src[start : src.index('async def lock_period(')]
        assert 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' in block
        assert 'await lock_period(session, period)' in block
        assert 'SessionLocal()' in block, '必须是独立连接（第一句才能设隔离级别）'

    def test_period_lock_is_transaction_scoped(self):
        """期间锁必须是**事务级**（`_xact_`）：会话级会在连接归还后残留。"""
        assert 'pg_advisory_xact_lock' in self._source()


# ------------------------------------------------- P2-4：单件体积不许提前舍入


class TestVolumeNotRoundedEarly:
    """第五批审查 P2：箱体积 0.0004 m³ ÷ 1000 件 = 4e-7，舍到 6 位就成 0，
    整批 100000 件算成 0 m³ —— 5000 元/m³ 的费率下运费 0 元（应为 200 元）。
    """

    class _Sku:
        def __init__(self, **kw):
            self.carton_volume = kw.get('carton_volume')
            self.carton_qty = kw.get('carton_qty')
            self.length = kw.get('length')
            self.width = kw.get('width')
            self.height = kw.get('height')

    def test_carton_path_keeps_precision(self):
        from app.modules.pricing.logistics import sku_unit_volume

        unit, source, problem = sku_unit_volume(
            self._Sku(carton_volume=Decimal('0.0004'), carton_qty=1000)
        )
        assert problem is None
        # 关键：**不等于 0**，也不能是"舍到 6 位"之后的 0.000000
        assert unit == Decimal('0.0004') / 1000
        assert unit > 0
        # 整批：先乘数量、再按存储精度舍
        batch = (unit * 100000).quantize(Decimal('0.0001'))
        assert batch == Decimal('0.0400')
        assert batch * 5000 == Decimal('200.0000')  # 5000 元/m³ → 200 元，不是 0

    def test_dimension_path_keeps_precision(self):
        """长宽高那条路也不许提前舍入（两个来源都要覆盖）。"""
        tiny = _volume_from_dimensions(
            self._Sku(length=Decimal('1'), width=Decimal('1'), height=Decimal('1'))
        )
        # 1 mm³ = 1e-9 m³：提前舍到 6 位会变 0
        assert tiny == Decimal('1') / Decimal('1000000000')
        assert (tiny * 1_000_000).quantize(Decimal('0.0001')) == Decimal('0.0010')

    def test_normal_sizes_unchanged(self):
        """普通尺寸不受影响（别把功能改坏）。"""
        unit, _source, _problem = __import__(
            'app.modules.pricing.logistics', fromlist=['sku_unit_volume']
        ).sku_unit_volume(self._Sku(carton_volume=Decimal('0.144'), carton_qty=20))
        assert unit == Decimal('0.0072')


# ------------------------------------------------- P2-5：专属费率优先


class TestSpecificRateWins:
    """第五批审查 P2：同承运商同为陆运，通用 1 元/kg、宁波→天津专属 10 元/kg，
    10kg 货物从前选中通用那条（10 元，还标成 match_level=1），应为专属的 100 元。
    """

    class _Rate:
        def __init__(self, provider, origin, destination, method='陆运', price='1'):
            self.provider = provider
            self.origin_region = origin
            self.destination_region = destination
            self.shipping_method = method
            self.unit_price_per_kg = Decimal(price)

    def test_specificity_score(self):
        assert rate_specificity(self._Rate('顺丰', None, None)) == 0
        assert rate_specificity(self._Rate('顺丰', '宁波', None)) == 1
        assert rate_specificity(self._Rate('顺丰', '宁波', '天津')) == 2

    def test_same_carrier_specific_beats_general(self):
        rows = [
            self._Rate('顺丰', None, None, price='1'),
            self._Rate('顺丰', '宁波', '天津', price='10'),
        ]
        kept = _prefer_specific(rows, origin='宁波', destination='天津')
        assert len(kept) == 1
        assert kept[0].unit_price_per_kg == Decimal('10'), '专属费率必须压过通用费率'
        # 10kg → 100 元（不是通用那条的 10 元）
        assert kept[0].unit_price_per_kg * 10 == Decimal('100')

    def test_general_rate_of_another_carrier_still_competes(self):
        """跨承运商比价**不变**：便宜的通用费率仍可赢过另一家的专属费率。"""
        rows = [
            self._Rate('顺丰', None, None, price='1'),
            self._Rate('德邦', '宁波', '天津', price='10'),
        ]
        kept = _prefer_specific(rows, origin='宁波', destination='天津')
        assert len(kept) == 2, '不同承运商都要留着比价'

    def test_carrier_with_only_general_rate_is_kept(self):
        """只有通用费率的承运商**不能**被整个排掉（比价就没得比了）。"""
        rows = [
            self._Rate('顺丰', '宁波', '天津', price='10'),
            self._Rate('中通', None, None, price='2'),
        ]
        kept = _prefer_specific(rows, origin='宁波', destination='天津')
        providers = sorted(r.provider for r in kept)
        assert providers == ['中通', '顺丰']

    def test_different_shipping_method_does_not_shadow(self):
        """运输方式不同就不是"同一件事"，不能互相压制。"""
        rows = [
            self._Rate('顺丰', None, None, method='陆运', price='1'),
            self._Rate('顺丰', '宁波', '天津', method='快递', price='10'),
        ]
        kept = _prefer_specific(rows, origin='宁波', destination='天津')
        assert len(kept) == 2

    def test_empty_input(self):
        assert _prefer_specific([], origin=None, destination=None) == []


# ------------------------------------------------- P2-3：任务编辑的版本校验


class TestTaskPatchVersionGuard:
    """第五批审查 P2：`TaskUpdate` 声明了 `expected_updated_at`，但 PATCH 没用它。
    实测库里版本已从 9月1日 更新为 10月1日，带着 9月1日 的旧版本编辑仍返回成功，
    把同事刚改的标题覆盖掉了。行锁刷新挡不住这个 —— 它保证"我拿到的是最新行"，
    但我要写的是**基于旧版本做的决定**。
    """

    def _source(self) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        return (root / 'app' / 'modules' / 'task' / 'router.py').read_text(encoding='utf-8')

    def _patch_block(self) -> str:
        src = self._source()
        start = src.index('async def update_task(')
        return src[start : src.index('async def ', start + 10)]

    def test_patch_endpoint_checks_version(self):
        block = self._patch_block()
        assert 'payload.expected_updated_at' in block, 'PATCH 必须用客户端版本做冲突判断'
        assert 'VERSION_CONFLICT' in block, '冲突要返回 409（与其它五个写入点一致）'

    def test_protocol_field_is_not_written_into_the_row(self):
        """`expected_updated_at` 是**协议字段**，不能进 setattr 循环写进库。

        写进去就是把"用户声称的版本"当成真实修改时间 —— `updated_at` 被污染后，
        后续别人再来的乐观锁校验会拿这个假版本去比，冲突检测直接失效。
        """
        block = self._patch_block()
        pop_at = block.index('changes.pop("expected_updated_at", None)')
        loop_at = block.index('for field, value in changes.items():')
        assert pop_at < loop_at, '必须在写入循环之前把它剔除'


class TestTaskVersionPrecision:
    """**第五批复审返修**：`replace(microsecond=0)` 让同一秒内的改动全部失效。

    复审实测：库里 `10:00:00.200000`、旧页面 `10:00:00.100000` → **保存成功**，
    覆盖了同事刚改的标题。我复现时更严重：拿一个**明显过期但同秒**的版本
    （差 800 微秒）也能改成功。

    根因是我从 `targets.py` 抄了"截到秒"的写法，理由是"HTTP 回传精度可能更低"——
    但**实测那两条时间戳都带微秒**（响应里 `…17.761026+00:00`，客户端原样回传），
    这个理由在这条路径上不成立；而代价是整个"同一秒内的并发保护"失效。

    这一组是**源码级**断言（离线环境起不了真接口），
    真接口的端到端验证见 `scripts/check_task_version_conflict.py`。
    """

    def test_no_microsecond_truncation(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / 'app' / 'modules' / 'task' / 'router.py').read_text(encoding='utf-8')
        start = src.index('async def update_task(')
        block = src[start : src.index('async def ', start + 10)]
        # 比版本的那几行里不许出现 microsecond 截断
        assert not re.search(r'updated_at\.replace\(microsecond=0\)', block), (
            '版本比较不许截掉微秒 —— 同一秒内的改动会被放行（返修项）'
        )

    def test_compares_full_timestamp_with_timezone_normalised(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / 'app' / 'modules' / 'task' / 'router.py').read_text(encoding='utf-8')
        start = src.index('async def update_task(')
        block = src[start : src.index('async def ', start + 10)]
        assert '_as_utc(task.updated_at) != seen' in block, (
            '要按完整时间戳比、并做时区归一（同一时刻不同偏移不算冲突）'
        )

    def test_same_as_the_other_five_write_points(self):
        """与完成/取消/改期/改派/转交**同一份口径**：那边一直是完整精度。

        同一个"客户端版本"概念在两处判据不一样，本身就是 bug。
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / 'app' / 'modules' / 'task' / 'router.py').read_text(encoding='utf-8')
        guard_start = src.index('async def _guard_task_field(')
        guard = src[guard_start : src.index('async def ', guard_start + 10)]
        assert 'replace(microsecond=0)' not in guard, (
            '另外五个写入点不许被"顺手统一"成截断写法'
        )
