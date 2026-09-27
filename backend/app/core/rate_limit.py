"""登录防爆破：进程内滑动窗口失败计数。

为什么放进程内：本项目部署口径是单实例（定时调度器也是同一约束，
见 12-交接文档），内存计数即可；将来真上多实例再换 Redis，接口不用变。

设计：
- 键 = 用户名 + 来源 IP（同一账号换 IP 不解锁，同一 IP 试别的账号互不影响）；
- 滑动窗口：窗口内失败满 N 次 → 锁定到最早一次失败滑出窗口，期间直接拒绝
  （不再耗 bcrypt 计算，这也是一道 DoS 缓冲）；
- 登录成功即清零。
"""

import threading
import time

_lock = threading.Lock()
# key -> 窗口内的失败时间戳（monotonic 秒）
_failures: dict[str, list[float]] = {}


def _prune(times: list[float], now: float, window: float) -> list[float]:
    return [t for t in times if now - t <= window]


def remaining_lock_seconds(
    key: str, *, max_attempts: int, window_minutes: int
) -> int:
    """key 还被锁多少秒；0 表示可以尝试。"""
    window = window_minutes * 60
    now = time.monotonic()
    with _lock:
        times = _prune(_failures.get(key, []), now, window)
        _failures[key] = times
        if len(times) >= max_attempts:
            return max(1, int(window - (now - times[0])))
        return 0


def register_failure(
    key: str, *, max_attempts: int, window_minutes: int
) -> int:
    """记一次登录失败，返回触发后的剩余锁定秒数（未触发为 0）。

    第 N 次失败的这一刻立即生效。
    """
    window = window_minutes * 60
    now = time.monotonic()
    with _lock:
        times = _prune(_failures.get(key, []), now, window)
        times.append(now)
        _failures[key] = times
        # 顺手清掉已经完全滑出窗口的空键，字典不会无限膨胀
        for k in [k for k, v in _failures.items() if not _prune(v, now, window)]:
            _failures.pop(k, None)
        if len(times) >= max_attempts:
            return max(1, int(window - (now - times[0])))
        return 0


def reset(key: str) -> None:
    """登录成功，清空该 key 的失败记录。"""
    with _lock:
        _failures.pop(key, None)
