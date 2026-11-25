"""P2/B07-05：对账核定金额可选，手工售后可不带金额。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.modules.erp.schema import DiffConfirmRequest  # noqa: E402

obj = DiffConfirmRequest(resolution="manual", note="人工处理")
print(f"[B07-05] resolution={obj.resolution} financial_adjustment_amount={obj.financial_adjustment_amount!r}")
