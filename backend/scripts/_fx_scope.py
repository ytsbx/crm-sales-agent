"""套件专用的「外贸口径」夹具：临时把业务口径放开 / 收回。

## 为什么需要它

业务口径是「**只做国内、币种固定人民币**」，服务端有一道真闸：
`trade_mode = domestic` 时，报价 / 手工建订单 / 订单草稿一律只收人民币
（`backend/app/core/trade_mode.py`）。

可有些套件**故意要造外币数据**（外币报价的汇率快照、多币种不混加、
外币定制行的成本口径…）。这恰好也是业务方的真实做法：**管理员先把口径
放开，再报外币价**。套件照做即可 —— 先 `both`，跑完 `domestic`。

## 两条纪律

1. **谁都别把口径留在 `both`**：后面所有套件都会以为可以写外币，
   于是一堆"外币断言"会莫名其妙地绿（或者更糟：悄悄写进外币数据）。
   所以用它的套件必须放在 `try/finally` 里收回来。
2. `ops/run_checks.sh` 在跑清单**之前**还有一道统一复位兜底 ——
   万一某个套件被硬杀（超时/SIGKILL）没来得及收回，也不会污染整轮回归。

⚠️ 本文件不是套件，别登记进 `ops/check_suites.txt`。
"""

import json
import os
import urllib.error
import urllib.request

DOMESTIC = "domestic"
BOTH = "both"


def set_trade_mode(token: str, mode: str) -> int:
    """把口径改成 `mode`，返回 HTTP 状态码（200 才算成功）。

    自己带一份最小的 HTTP 调用，是为了让套件 import 时不必关心它的 `call()`
    长什么样 —— 各套件的 `call()` 签名并不统一。
    """
    base = (os.environ.get("API_BASE") or "").rstrip("/")
    if not base:
        raise SystemExit("必须显式设置 API_BASE（口径夹具不知道后端在哪）")
    data = json.dumps({"key": "trade_mode", "value": {"mode": mode}}).encode()
    req = urllib.request.Request(
        base + "/settings", data=data, method="PATCH"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def open_export(token: str) -> None:
    """放开成「国内与出口都做」。"""
    status = set_trade_mode(token, BOTH)
    if status != 200:
        raise SystemExit(f"放开口径失败：HTTP {status}（没法按业务方的做法造外币数据）")


def restore_domestic(token: str) -> None:
    """收回成「只做国内」。**收不回去就等于污染整轮回归。**"""
    set_trade_mode(token, DOMESTIC)  # 尽力而为，不再抛：它是 finally 里的最后一步
