"""
B21–B23 高严重度复核复现（证伪用）。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b21_b23_highlights.py -q

B21 报告自身 0 P1；本批覆盖 B22-01..03、B23-01。
"""
from __future__ import annotations

import inspect

import pytest

from app.core.errors import AppError


# ---------------------------------------------------------------- B22-01
def test_b22_01_owner_should_be_checked_against_scope():
    """打样负责人/制作负责人只判存在+active，不判数据范围。"""
    from app.modules.sample import router as sample_router

    source = inspect.getsource(sample_router._assert_active_owner)
    assert "scoped_owner_ids" in source or "ensure_in_scope" in source, (
        "_assert_active_owner 没有范围校验；sample:manage 可把单子指到范围外用户"
    )


# ---------------------------------------------------------------- B22-02
def test_b22_02_approve_should_reject_self_approval_for_non_admin():
    """本地审批只要求 sample:manage，创建人可自批。"""
    from app.modules.sample import router as sample_router

    source = inspect.getsource(sample_router.approve_sample)
    assert "created_by" in source, (
        "approve_sample 不比较 created_by 与 user.id，非管理员也能审批自己提交的单"
    )


# ---------------------------------------------------------------- B22-03
def test_b22_03_material_fields_should_reopen_approval():
    """单头上的材质/规格/印刷/包装/工艺要求是审批依据，却不在 PART_LOCK_FIELDS。

    PART_LOCK_LABEL 写的是“材质、工艺、图纸版本、目标完成日、验收标准”，
    但 PART_LOCK_FIELDS 只有后两项；已批准后改材质/规格，状态仍 approved。
    """
    from app.modules.sample.router import PART_LOCK_FIELDS

    for field in (
        "sample_fee",
        "estimated_hours",
        "production_owner_id",
        "material_spec",
        "product_size",
        "printing",
        "accessories",
        "packaging",
        "craft_requirement",
    ):
        assert field in PART_LOCK_FIELDS, (
            f"{field} 不在 PART_LOCK_FIELDS：已批准后改动不会退回重审，审批依据会与实物不一致"
        )


# ---------------------------------------------------------------- B23-01
def test_b23_01_huge_max_amount_should_be_rejected():
    """max_amount 只校验格式，不限位数；100 位整数会 float 成 inf。"""
    from app.modules.settings.service import _strict_amount

    with pytest.raises(AppError):
        _strict_amount("9" * 100, "第 1 档的金额上限")


def test_b23_01_empty_role_codes_should_be_rejected():
    """role_codes 为空 list 会静默跳过角色校验，任何 quote:approve 都能批该档。"""
    from app.modules.settings import service as settings_service

    source = inspect.getsource(settings_service.validate_setting_value)
    assert "if not role_codes" in source or "role_codes 不能为空" in source, (
        "approval_levels 允许空 role_codes；access.py 会因 allowed_roles 为空跳过角色限制"
    )
