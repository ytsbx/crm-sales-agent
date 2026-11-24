"""P2/B04-20：发货批次未传 request_key 时，幂等键为 None。"""
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.idempotency import request_key_from  # noqa: E402


def main():
    req = SimpleNamespace(headers={})
    print(f"[B04-20] 无 body key、无 X-Request-Key -> {request_key_from(req, None)!r}")
    print(f"[B04-20] 只给 body key -> {request_key_from(req, 'abc')!r}")
    print(f"[B04-20] 只给 header -> {request_key_from(SimpleNamespace(headers={'x-request-key': 'hdr'}), None)!r}")


if __name__ == "__main__":
    main()
