"""Prometheus 指标（双轨可观测的第二轨；第一轨是 ai_call_log 业务表 + 后台页面）。

命名规范：业务前缀 ai_，单位后缀 _seconds / _total；label 基数必须可控
（endpoint 只有 4 个值、reason 是枚举，绝不把 uid / trace_id 放进 label）。
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0)

http_requests_total = Counter(
    "ai_http_requests_total",
    "HTTP 请求数",
    ["endpoint", "code"],
    registry=REGISTRY,
)
http_duration_seconds = Histogram(
    "ai_http_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["endpoint"],
    buckets=DURATION_BUCKETS,
    registry=REGISTRY,
)
http_inflight = Gauge(
    "ai_http_inflight",
    "正在处理的请求数",
    ["endpoint"],
    registry=REGISTRY,
)

llm_requests_total = Counter(
    "ai_llm_requests_total",
    "上游模型调用次数",
    ["provider", "kind", "outcome"],
    registry=REGISTRY,
)
llm_duration_seconds = Histogram(
    "ai_llm_duration_seconds",
    "上游模型调用耗时（秒）",
    ["kind"],
    buckets=DURATION_BUCKETS,
    registry=REGISTRY,
)
llm_tokens_total = Counter(
    "ai_llm_tokens_total",
    "上游模型 token 消耗",
    ["provider", "kind", "direction"],
    registry=REGISTRY,
)
llm_retries_total = Counter(
    "ai_llm_retries_total",
    "重试次数",
    ["reason"],
    registry=REGISTRY,
)

circuit_breaker_state = Gauge(
    "ai_circuit_breaker_state",
    "熔断器状态：0=closed 1=half_open 2=open",
    ["breaker"],
    registry=REGISTRY,
)
circuit_breaker_trips_total = Counter(
    "ai_circuit_breaker_trips_total",
    "熔断器打开次数",
    ["breaker"],
    registry=REGISTRY,
)

degradations_total = Counter(
    "ai_degradations_total",
    "降级触发次数",
    ["reason"],
    registry=REGISTRY,
)
queue_rejected_total = Counter(
    "ai_queue_rejected_total",
    "因背压被拒绝的请求数（503）",
    ["endpoint"],
    registry=REGISTRY,
)
idempotency_total = Counter(
    "ai_idempotency_total",
    "幂等键命中情况",
    ["result"],
    registry=REGISTRY,
)

rag_queries_total = Counter(
    "ai_rag_queries_total",
    "RAG 检索结果",
    ["result"],
    registry=REGISTRY,
)
rag_top_score = Histogram(
    "ai_rag_top_score",
    "RAG 命中最高相似度",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=REGISTRY,
)
kb_documents = Gauge(
    "ai_kb_documents",
    "知识库各类文档条数",
    ["doc_type"],
    registry=REGISTRY,
)
kb_last_sync_timestamp = Gauge(
    "ai_kb_last_sync_seconds",
    "上一次知识库同步完成时间（unix 秒）",
    registry=REGISTRY,
)

build_info = Gauge(
    "ai_build_info",
    "服务版本信息",
    ["version", "provider"],
    registry=REGISTRY,
)


def render() -> bytes:
    return generate_latest(REGISTRY)


def content_type() -> str:
    return "text/plain; version=0.0.4; charset=utf-8"
