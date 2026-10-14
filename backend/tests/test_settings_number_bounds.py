"""系统配置里的**数值/比率**类字段必须在写侧就有上下界（2026-10-14 审查修）。

## 这条测试守的是什么

读侧一直用 `settings.service.get_number`，而它**只防 NaN/Inf/类型错，
不查上下界**。于是把配置写成负数/0/超大值，会被**原样**拿去跑业务，而且不报错：

| 配置 | 写坏后的后果（实测/推演） |
|---|---|
| `quote_valid_days = -30` | 新建报价 `valid_until = 今天-30` → **一出生就过期**；同时履约保护里 `Quote.valid_until >= today` 不再成立 → 客户被错误回收 |
| `customer_active_days / customer_stale_days = 0` | 两个 cutoff 反转 → 全公司客户被判"沉睡/活跃" |
| `export.limit = 0` | 所有导出报 413「超过单次上限 0」 |
| `default_min_margin = 1.5` | 授权利润率被放大 → 低价审批门槛静默消失 |
| `default_min_margin = -0.2` | 门槛为负 → 什么都拦 |

零依赖（不需要数据库）的用例走 `_validate_number_settings`；
需要查权限表的（`pool_recycle_restore_permission`）不在本文件范围。
"""
import pytest

from app.core.errors import AppError
from app.modules.settings.service import (
    SETTING_NUMBER_RULES,
    SETTING_RATIO_RULES,
    _strict_ratio,
    _validate_number_settings,
)


def _rejects(key: str, value: dict) -> bool:
    """写侧会不会拒（True=拒）。"""
    try:
        _validate_number_settings(key, value)
        return False
    except AppError:
        return True


@pytest.mark.parametrize(
    'key,value,why',
    [
        # ---- 天数类：负 / 0 / 小数 都要拒 ----
        ('quote_valid_days', {'days': -30}, '新建报价会一出生就过期'),
        ('quote_valid_days', {'days': 0}, '有效期 0 天'),
        ('quote_valid_days', {'days': 0.5}, '小数会被 int 截断，保存值≠执行值'),
        ('customer_active_days', {'days': 0}, 'cutoff 反转，全公司客户判沉睡'),
        ('customer_stale_days', {'days': -1}, '负数天数'),
        ('opportunity_risk_days', {'days': -1}, '负数天数'),
        ('opportunity_stale_days', {'days': 0}, '0 天'),
        ('contract', {'monthly_remind_days': -5}, '负数天数'),
        # ---- 行数/系数类 ----
        ('export', {'limit': 0}, '所有导出报 413「上限 0」'),
        ('export', {'limit': -1}, '负数上限'),
        ('export', {'alert_rows': -5}, '负数告警行数'),
        ('export', {'alert_window_hours': 0}, '0 小时窗口'),
        ('logistics_volumetric_ratio', {'number': 0}, '系数 0 会让体积重变 0'),
        # ---- 比率类：越界一律拒 ----
        ('default_min_margin', {'ratio': 1.5}, '门槛被放大 → 低价审批静默消失'),
        ('default_min_margin', {'ratio': -0.2}, '门槛为负 → 什么都拦'),
        ('default_target_margin', {'ratio': 2}, '目标毛利率 >1'),
        ('price_range_ratio', {'ratio': 999}, '区间比例离谱'),
        ('hard_floor', {'markup_ratio': 2}, '加成比例 >1'),
        ('export_tax_refund_rate', {'ratio': 1.01}, '退税率 >1'),
        # ---- 非数字/布尔 ----
        ('default_min_margin', {'ratio': True}, '布尔会被当 1 收下'),
        ('quote_valid_days', {'days': True}, '布尔会被当 1 收下'),
        ('quote_valid_days', {'days': 'abc'}, '非数字'),
        ('default_min_margin', {'ratio': 'abc'}, '非数字'),
    ],
)
def test_invalid_numbers_are_rejected(key, value, why):
    assert _rejects(key, value), f'应当拒绝：{key}={value}（{why}）'


@pytest.mark.parametrize(
    'key,value',
    [
        ('quote_valid_days', {'days': 30}),
        ('quote_valid_days', {'days': '30'}),  # 表单可能传数字串
        ('customer_active_days', {'days': 30}),
        ('customer_stale_days', {'days': 14}),
        ('export', {'limit': 5000}),
        ('export', {'alert_rows': 20000}),
        ('export', {'limit': 5000, 'alert_rows': 20000}),  # 两个一起传
        ('default_min_margin', {'ratio': 0.15}),
        ('default_min_margin', {'ratio': 0}),
        ('default_min_margin', {'ratio': 1}),
        ('price_range_ratio', {'ratio': 0.04}),
        ('export_tax_refund_rate', {'ratio': 0.13}),
        ('pool_recycle_notice_days', {'days': 7}),
        ('logistics_volumetric_ratio', {'number': 6000}),
    ],
)
def test_valid_numbers_pass(key, value):
    _validate_number_settings(key, value)


@pytest.mark.parametrize(
    'key,value',
    [
        ('export', {'limit': 5000}),
        ('export', {'alert_rows': 20000}),
        ('export', {'alert_window_hours': 24}),
    ],
)
def test_partial_update_only_checks_the_field_that_was_sent(key, value):
    """★ 只改一个字段时必须能过。

    前端写的是 `{[key]: next}`（整对象替换），所以**一个 key 的多个数值字段
    不会一起提交**。判据若按 key 索引、把没传的字段也拿去校验，
    「只改 limit」就会因为 `alert_rows` 是 None 而被拒 ——
    我第一版就是这个形态，实测 `export.limit=5000` 报"导出告警行数必须是整数天"。
    """
    _validate_number_settings(key, value)


def test_untouched_key_is_not_validated():
    """别的 key 的坏值不该影响本次保存（判据必须按 key 过滤）。"""
    _validate_number_settings('some_other_key', {'days': -999})
    _validate_number_settings('export', {})  # 空对象：什么都没改


def test_rules_cover_every_field_read_by_get_number():
    """读侧用到的数值字段必须在规则表里 —— 防"新加一个配置但忘了加界"。

    这里的清单是 `grep get_number` 的结果（人工核对过一次）；
    新增配置时这份清单和规则表要一起改，否则本用例会红。
    """
    read_by_get_number = {
        ('quote_valid_days', 'days'),
        ('customer_active_days', 'days'),
        ('customer_stale_days', 'days'),
        ('opportunity_risk_days', 'days'),
        ('opportunity_stale_days', 'days'),
        ('contract', 'monthly_remind_days'),
        ('export', 'limit'),
        ('export', 'alert_rows'),
        ('export', 'alert_window_hours'),
        ('logistics_volumetric_ratio', 'number'),
    }
    covered = set(SETTING_NUMBER_RULES)
    missing = read_by_get_number - covered
    assert not missing, f'这些数值配置读侧在用、写侧却没有上下界：{sorted(missing)}'

    read_ratios = {
        ('default_min_margin', 'ratio'),
        ('default_target_margin', 'ratio'),
        ('price_range_ratio', 'ratio'),
        ('hard_floor', 'markup_ratio'),
        ('export_tax_refund_rate', 'ratio'),
    }
    assert not (read_ratios - set(SETTING_RATIO_RULES))


@pytest.mark.parametrize('bad', [-0.01, 1.01, float('nan'), float('inf'), True, 'x', None])
def test_strict_ratio_rejects_out_of_range_and_junk(bad):
    with pytest.raises(AppError):
        _strict_ratio(bad, '测试比率')


@pytest.mark.parametrize('good', [0, 1, 0.5, '0.25'])
def test_strict_ratio_accepts_valid(good):
    assert 0 <= _strict_ratio(good, '测试比率') <= 1
