"""客户查重：短名/纯数字名不能靠“包含”就判重复。"""
from app.core.similarity import similarity_score


def test_short_numeric_name_does_not_match_substring():
    # 客户名只写 "11" 时，不能命中手机号/门牌号里带 11 的客户。
    score, reasons = similarity_score(
        left={"name": "11"},
        right={"name": "AAA19891181602"},
        weights={"weight_name_contains": 55, "threshold": 50},
    )
    assert score == 0
    assert reasons == []


def test_exact_short_name_still_matches():
    # 两个客户都真的叫 "11" 时，完全相等仍然要提示。
    score, reasons = similarity_score(
        left={"name": "11"},
        right={"name": "11"},
        weights={"weight_name_exact": 60, "threshold": 50},
    )
    assert score == 60
    assert reasons == ["企业名称一致"]


def test_long_name_containment_still_matches():
    # "宁波宏远" 是 "宁波宏远包装有限公司" 的有效主体，仍应提示。
    score, reasons = similarity_score(
        left={"name": "宁波宏远"},
        right={"name": "宁波宏远包装有限公司"},
        weights={"weight_name_contains": 55, "threshold": 50},
    )
    assert score == 55
    assert reasons == ["企业名称互相包含"]


def test_long_numeric_name_does_not_match_substring():
    # 纯数字长名（订单号/编号）同样不做包含匹配，避免误报。
    score, reasons = similarity_score(
        left={"name": "12345"},
        right={"name": "订单12345"},
        weights={"weight_name_contains": 55, "threshold": 50},
    )
    assert score == 0
    assert reasons == []
