"""重试策略单元测试：只对连接失败和 5xx 重试，超时和 4xx 绝不重试。"""

import pytest

from app.resilience import RetryPolicy


def test_no_retry_on_timeout():
    p = RetryPolicy(max_attempts=1)
    from httpx import ReadTimeout
    assert not p.should_retry(0, exc=ReadTimeout("t"))


def test_retry_on_connect_error():
    p = RetryPolicy(max_attempts=1)
    from httpx import ConnectError
    assert p.should_retry(0, exc=ConnectError("c"))


def test_retry_on_5xx():
    p = RetryPolicy(max_attempts=1)
    assert p.should_retry(0, status=503)


def test_no_retry_on_4xx():
    p = RetryPolicy(max_attempts=1)
    assert not p.should_retry(0, status=401)


def test_no_retry_after_max_attempts():
    p = RetryPolicy(max_attempts=1)
    from httpx import ConnectError
    assert not p.should_retry(1, exc=ConnectError("c"))


def test_zero_max_attempts_never_retries():
    p = RetryPolicy(max_attempts=0)
    from httpx import ConnectError
    assert not p.should_retry(0, exc=ConnectError("c"))
