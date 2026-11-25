"""B18-09：案例入参边界回归（标题非空白 / 标签数量与单项上限 / customer_id>0）。"""
import pytest
from pydantic import ValidationError

from app.modules.cases.schema import CaseCreate, CaseUpdate


def test_case_create_customer_id_zero_rejected():
    with pytest.raises(ValidationError):
        CaseCreate(title="案例", customer_id=0)
    assert CaseCreate(title="案例", customer_id=1).customer_id == 1


def test_case_title_blank_rejected():
    with pytest.raises(ValidationError):
        CaseCreate(title="   ")
    with pytest.raises(ValidationError):
        CaseUpdate(title="  ")
    with pytest.raises(ValidationError):
        CaseUpdate(title=None)
    assert CaseUpdate(title="  改  ").title == "改"


def test_case_problem_tags_bounded():
    with pytest.raises(ValidationError):
        CaseCreate(title="案例", problem_tags=["标签"] * 21)
    with pytest.raises(ValidationError):
        CaseCreate(title="案例", problem_tags=["x" * 33])
    with pytest.raises(ValidationError):
        CaseCreate(title="案例", problem_tags=["  "])
    assert CaseCreate(title="案例", problem_tags=[" 价格异议 "]).problem_tags == ["价格异议"]