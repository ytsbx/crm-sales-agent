"""F10-01 当前代码实测：只有企微权限、没有客户权限时，三个入口都应 403。

运行：backend/.venv/bin/python review/verification/p0_f10_permission_gates.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.core.deps import CurrentUser, get_current_user  # noqa: E402
from app.core.errors import register_exception_handlers  # noqa: E402
from app.modules.wecom.router import router  # noqa: E402


app = FastAPI()
app.include_router(router, prefix="/api/v1")
register_exception_handlers(app)
app.dependency_overrides[get_db] = lambda: AsyncMock()
user = CurrentUser(
    SimpleNamespace(
        id=7,
        name="只有企微权限",
        username="wecom_only",
        department_id=None,
        initial_password_pending=False,
    ),
    permissions={"wecom:view", "wecom:manage"},
    roles=["salesperson"],
    data_scope="self",
)
app.dependency_overrides[get_current_user] = lambda: user
client = TestClient(app)

cases = [
    ("GET", "/api/v1/integrations/wecom/unbound-contacts/8/candidates", None, "customer:view"),
    ("POST", "/api/v1/integrations/wecom/unbound-contacts/8/bind-customer", {"customer_id": 1}, "customer:update"),
    ("POST", "/api/v1/integrations/wecom/unbound-contacts/8/create-customer", {"name": "测试客户"}, "customer:create"),
]
failed = False
for method, path, body, needed in cases:
    response = client.request(method, path, json=body)
    print(f"{method} {path} -> {response.status_code} {response.text}")
    if response.status_code != 403 or needed not in response.text:
        failed = True
print("F10-01 RESULT:", "FAILED" if failed else "BLOCKED (PASS)")
sys.exit(1 if failed else 0)
