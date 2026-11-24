"""P2/B20-03、B21-02：商机负责人可清空；打样字段长度现状。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError  # noqa: E402
from app.modules.opportunity.schema import OpportunityAssign  # noqa: E402
from app.modules.sample.schema import SampleItemInput, SampleConfirm, SampleApprove, SampleShip  # noqa: E402

obj = OpportunityAssign(owner_id=None)
print(f"[B20-03] owner_id={obj.owner_id!r} 通过")

def check(label, model, **kwargs):
    try:
        model(**kwargs)
        print(f"[{label}] 通过")
    except ValidationError as exc:
        print(f"[{label}] 被拒 {exc.errors()[0]['type']}")

check("B21-02-item_name", SampleItemInput, item_name="x" * 300)
check("B21-02-confirm", SampleConfirm, accepted=True, remark="x" * 300)
check("B21-02-approve", SampleApprove, approved=False, reject_reason="x" * 300)
check("B21-02-ship", SampleShip, carrier="x" * 300, tracking_no="y" * 300)
