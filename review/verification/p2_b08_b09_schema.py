"""P2/B08-03、B09-03：当前入参长度约束实测。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError  # noqa: E402
from app.modules.bizdoc.router import TemplateCreate  # noqa: E402
from app.modules.approval.rules_router import RulePayload  # noqa: E402
from app.modules.approval.schema import ApprovalDefinitionUpdate  # noqa: E402


def show(label, model, **kwargs):
    try:
        model(**kwargs)
        print(f"[{label}] 通过")
    except ValidationError as exc:
        print(f"[{label}] 被拒 {exc.errors()[0]['type']}")


show("B08-03", TemplateCreate, doc_type="quote", name="t", remark="x" * 300)
show("B09-03-desc", RulePayload, name="r", kind="quote", conditions=[{"field": "amount", "value": 1}], description="x" * 300)
show("B09-03-update", ApprovalDefinitionUpdate, name="x" * 300, business_type="b" * 100, status="s" * 100)
obj = ApprovalDefinitionUpdate(name="x" * 300, business_type="b" * 100, status="s" * 100)
print(f"[B09-03-update] 实际 name={len(obj.name)} business_type={len(obj.business_type)} status={len(obj.status)}")
