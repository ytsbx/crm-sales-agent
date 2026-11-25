"""统一社会信用代码（税号）国标校验与规范化（B13-02 第二层）。"""
import pytest
from pydantic import ValidationError

from app.core.similarity import similarity_score
from app.core.tax_no import normalize_tax_no, validate_tax_no
from app.modules.customer.schema import CustomerCreate, CustomerUpdate

VALID = "91440300708461136T"  # 腾讯计算机系统有限公司（真实合法示例）


def test_valid_code_accepted_and_normalized():
    assert validate_tax_no(VALID) == VALID
    assert validate_tax_no(" 91440300 708461136t ") == VALID


def test_fullwidth_normalized():
    assert normalize_tax_no("９１４４０３００７０８４６１１３６Ｔ") == VALID


def test_blank_or_none_becomes_none():
    assert validate_tax_no(None) is None
    assert validate_tax_no("   ") is None


def test_wrong_check_digit_rejected():
    with pytest.raises(ValueError, match="校验位"):
        validate_tax_no("91440300708461136X")


def test_illegal_letter_rejected():
    with pytest.raises(ValueError, match="I"):
        validate_tax_no("91440300I08461136T")


def test_legacy_fifteen_digit_rejected():
    with pytest.raises(ValueError, match="15 位"):
        validate_tax_no("123456789012345")


def test_wrong_length_rejected():
    with pytest.raises(ValueError, match="18 位"):
        validate_tax_no("91330100")


def test_customer_create_rejects_invalid_tax_no():
    with pytest.raises(ValidationError, match="校验位"):
        CustomerCreate(name="甲", tax_no="91440300708461136X")


def test_customer_create_normalizes_tax_no():
    obj = CustomerCreate(name="甲", tax_no=" 91440300 708461136t ")
    assert obj.tax_no == VALID


def test_customer_update_rejects_invalid_tax_no():
    with pytest.raises(ValidationError, match="18 位"):
        CustomerUpdate(tax_no="123")


def test_customer_update_blank_clears_tax_no():
    assert CustomerUpdate(tax_no="  ").tax_no is None


def test_similarity_treats_normalized_tax_no_as_same():
    score, reasons = similarity_score(
        left={"name": "甲公司", "tax_no": VALID},
        right={"name": "完全不同", "tax_no": "91440300 708461136t"},
        weights={"weight_tax_no": 60},
    )
    assert "统一社会信用代码一致" in reasons
    assert score >= 60
