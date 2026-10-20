"""健康检查与 trace 契约测试。

TestClient 必须作为 context manager 使用，否则 lifespan 不会执行、app.state 为空。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("KB_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("KB_COLLECTION", "test_health")
    monkeypatch.setenv("EMBED_DIM", "32")
    app = create_app()
    # 关键：with 块触发 lifespan startup/shutdown
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body
    assert "breaker" in body
    assert "kb" in body


def test_trace_id_echoed_in_response_header(client):
    tid = "abc12345-test-trace-id"
    r = client.post(
        "/api/chat",
        json={"user_msg_id": "t1", "uid": 1, "message": "hi"},
        headers={"X-Trace-Id": tid},
    )
    assert r.headers.get("x-trace-id") == tid


def test_invalid_trace_id_replaced(client):
    r = client.post(
        "/api/chat",
        json={"user_msg_id": "t2", "uid": 1, "message": "hi"},
        headers={"X-Trace-Id": "bad!@#$%^&*()"},
    )
    returned = r.headers.get("x-trace-id")
    assert returned is not None
    assert returned != "bad!@#$%^&*()"
    assert len(returned) >= 8


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "ai_http_requests_total" in r.text
    assert "ai_llm_requests_total" in r.text
