"""登录防爆破限流的单元测试（进程内滑动窗口，纯逻辑）。"""

from app.core import rate_limit


def setup_function():
    # 每个用例独立状态，避免互相污染
    with rate_limit._lock:
        rate_limit._failures.clear()


def test_lock_after_max_attempts():
    key = "u1|1.2.3.4"
    for _ in range(4):
        left = rate_limit.register_failure(key, max_attempts=5, window_minutes=10)
        assert left == 0
    # 第 5 次失败立即锁定
    left = rate_limit.register_failure(key, max_attempts=5, window_minutes=10)
    assert left > 0
    assert rate_limit.remaining_lock_seconds(key, max_attempts=5, window_minutes=10) > 0


def test_reset_on_success():
    key = "u2|1.2.3.4"
    for _ in range(5):
        rate_limit.register_failure(key, max_attempts=5, window_minutes=10)
    rate_limit.reset(key)
    assert rate_limit.remaining_lock_seconds(key, max_attempts=5, window_minutes=10) == 0


def test_different_keys_isolated():
    bad = "u3|1.2.3.4"
    other = "u4|1.2.3.4"
    for _ in range(5):
        rate_limit.register_failure(bad, max_attempts=5, window_minutes=10)
    assert rate_limit.remaining_lock_seconds(bad, max_attempts=5, window_minutes=10) > 0
    # 换个账号/换台机器互不影响
    assert rate_limit.remaining_lock_seconds(other, max_attempts=5, window_minutes=10) == 0


def test_window_slides():
    """窗口外（手动把时间戳拨老）的失败不再计数。"""
    key = "u5|1.2.3.4"
    for _ in range(5):
        rate_limit.register_failure(key, max_attempts=5, window_minutes=10)
    # 把失败时间戳整体拨回 11 分钟前（窗口 10 分钟）
    with rate_limit._lock:
        rate_limit._failures[key] = [t - 660 for t in rate_limit._failures[key]]
    assert rate_limit.remaining_lock_seconds(key, max_attempts=5, window_minutes=10) == 0
