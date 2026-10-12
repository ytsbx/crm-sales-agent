"""Read-only backend readiness check, including database connectivity."""

import argparse
import json
import time
import urllib.error
import urllib.request


def is_ready(base_url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base_url.rstrip("/") + "/health", timeout=2) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return False
    data = payload.get("data")
    return isinstance(data, dict) and data.get("status") == "up" and data.get("database") == "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--attempts", type=int, default=30)
    args = parser.parse_args()
    for attempt in range(args.attempts):
        if is_ready(args.base_url):
            print("backend ready (database ok)")
            return 0
        if attempt + 1 < args.attempts:
            time.sleep(1)
    print(f"后端健康检查未通过（含数据库）：{args.base_url}/health")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
