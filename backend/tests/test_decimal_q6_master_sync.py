"""Q6 决策3 边界回归：master 三链拆分——快照精确字符串、比较精确归一、指纹零换血。

覆盖：_json_value Decimal→原值字符串；'1.90'/1.9/Decimal('1.900') 归一同键
（不刷伪差异/伪"已修改"）；16 位边界老 float 糊值与新精确值判不同（修漏同步）；
指纹原料还原老形状→存量 diff_key 逐字稳定；文本字段指纹不受影响。
"""
from decimal import Decimal as D

from app.modules.product import master

BIG = "99999999999999.99"


def test_json_value_stores_exact_string():
    assert master._json_value(D(BIG)) == BIG
    assert master._json_value(D("1.90")) == "1.90"
    assert master._json_value("A") == "A"
    assert master._json_value(None) is None


def test_compare_normalizer_unifies_numeric_shapes():
    keys = {
        master._normalize_for_compare("1.90"),
        master._normalize_for_compare(1.9),
        master._normalize_for_compare(D("1.900")),
        master._normalize_for_compare("1.9"),
    }
    assert len(keys) == 1
    assert master._normalize_for_compare(None) == ""
    assert master._normalize_for_compare("A") == "A"


def test_compare_normalizer_detects_boundary_change_legacy_float_missed():
    legacy = float(D(BIG))  # 老 float 行糊成 .98
    assert master._normalize_for_compare(legacy) != master._normalize_for_compare(BIG)


def test_diff_key_stable_across_string_and_legacy_float_shapes():
    kwargs = dict(
        system_type="jst", shop_id="s1", sku_id=7, external_code="E1",
        diff_type="field_conflict", field_name="weight",
    )
    legacy = master.build_sku_diff_key(
        values={"current": 1.9, "incoming": 1.9}, **kwargs)
    new = master.build_sku_diff_key(
        values={"current": "1.9", "incoming": "1.90"}, **kwargs)
    assert legacy == new
    # 整数字段：老 int 形状与新字符串还原 int 后键稳定
    kwargs_int = {**kwargs, "field_name": "moq"}
    assert master.build_sku_diff_key(
        values={"current": 5, "incoming": 5}, **kwargs_int) == master.build_sku_diff_key(
        values={"current": "5", "incoming": "5"}, **kwargs_int)


def test_diff_key_text_field_untouched():
    kwargs = dict(
        system_type="jst", shop_id="s1", sku_id=7, external_code="E1",
        diff_type="field_conflict", field_name="color",
    )
    assert master.build_sku_diff_key(
        values={"current": "红", "incoming": "红"}, **kwargs) == master.build_sku_diff_key(
        values={"current": "红", "incoming": "红"}, **kwargs)