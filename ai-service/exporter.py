"""PHP 侧指标 exporter（窗口4 可观测轨道二）。

读 CRMEB MySQL 的 eb_ai_call_log 埋点表（AiCallLog 写入的终态记录）与 Redis
队列深度，以 Prometheus text format 暴露 :9877/metrics。与 ai-service 自身的
/metrics（进程内 prometheus_client）互补：这边是「PHP 链路」的真实视角。

增量模式：内存游标记录上次处理的最大 id，每次抓取只 observe 新增行；
进程重启从当前 max(id) 起步（不回灌历史，counter 语义在进程生命周期内成立）。
只读：一个 SELECT + 一个 LLEN，绝不写库。

本地运行：.venv/Scripts/python exporter.py   （默认 127.0.0.1:9877）
compose 运行：容器内 python exporter.py，连接 crmeb_mysql / crmeb_redis。
"""

from __future__ import annotations

import os
import time
from collections import defaultdict

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from pymysql import Connection

LISTEN_HOST = os.getenv("EXPORTER_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("EXPORTER_PORT", "9877"))

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "33061"))
MYSQL_USER = os.getenv("MYSQL_USER", "crmeb")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "123456")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "crmeb")
TABLE = os.getenv("MYSQL_PREFIX", "eb_") + "ai_call_log"

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "63791"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "123456")
QUEUE_KEY = os.getenv("QUEUE_KEY", "CRMEB6ad6fadaa57bf")

SCRAPE_INTERVAL = float(os.getenv("EXPORTER_INTERVAL", "2"))  # 兜底轮询间隔；拉取时以游标增量为主

# ---- 指标（label 基数可控：outcome 5 值 / event 10 值 / reason 枚举，绝不放 uid/trace_id）----

calls_total = Counter(
    "ai_php_calls_total",
    "AI 调用终态次数（按事件与结果）",
    ["event", "outcome"],
)
outcome_total = Counter(
    "ai_php_outcome_total",
    "AI 调用终态次数（按归一化结果）",
    ["outcome"],
)
degraded_total = Counter(
    "ai_php_degraded_total",
    "降级次数（按原因，reason 为空归为 unknown）",
    ["reason"],
)
latency_total_ms = Histogram(
    "ai_php_total_ms",
    "job 全程耗时（成功应答样本）",
    buckets=(100, 250, 500, 1000, 2000, 3000, 5000, 8000, 12000, 15000, 20000),
)
http_ms = Histogram(
    "ai_php_http_ms",
    "HTTP 往返耗时（成功应答样本）",
    buckets=(50, 100, 250, 500, 1000, 2000, 3000, 5000, 10000, 15000, 20000),
)
attempts_total = Counter(
    "ai_php_http_attempts_total",
    "HTTP 尝试总次数（>调用数即发生重试）",
)
tokens_total = Counter(
    "ai_php_tokens_total",
    "token 消耗",
    ["direction"],
)
kb_used_total = Counter(
    "ai_php_kb_used_total",
    "RAG 命中知识库的调用次数",
)
queue_depth = Gauge(
    "ai_php_queue_depth",
    "PHP think-queue 待处理任务数（redis LLEN）",
)
rows_seen = Gauge(
    "ai_php_exporter_last_id",
    "exporter 已处理的最大 ai_call_log.id",
)
scrape_errors_total = Counter(
    "ai_php_exporter_scrape_errors_total",
    "exporter 抓取失败次数",
)


def mysql_conn() -> Connection:
    conn = Connection(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        connect_timeout=3,
        read_timeout=5,
        charset="utf8mb4",
    )
    # 必须 autocommit：pymysql 默认关闭，长连接会停留在 REPEATABLE READ 快照里，
    # 永远读不到其他进程新写入的行（实测 last_id 停滞不前的根因）
    conn.autocommit(True)
    return conn


def poll_once(conn: Connection, cursor: int) -> tuple[int, int]:
    """处理 id > cursor 的新增行，返回 (新游标, 处理行数)。"""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, event, outcome, reason, http_ms, ai_ms, total_ms, attempts, "
            f"kb_used, degraded, prompt_tokens, completion_tokens "
            f"FROM {TABLE} WHERE id > %s ORDER BY id ASC LIMIT 2000",
            (cursor,),
        )
        rows = cur.fetchall()
    for row in rows:
        (
            row_id, event, outcome, reason, http_ms_v, _ai_ms, total_ms_v,
            attempts, kb_used, degraded, prompt, completion,
        ) = row
        event = event or "unknown"
        outcome = outcome or "unknown"
        calls_total.labels(event=event, outcome=outcome).inc()
        outcome_total.labels(outcome=outcome).inc()
        if degraded:
            degraded_total.labels(reason=reason or "unknown").inc()
        if attempts:
            attempts_total.inc(int(attempts))
        if kb_used:
            kb_used_total.inc()
        if prompt:
            tokens_total.labels(direction="prompt").inc(int(prompt))
        if completion:
            tokens_total.labels(direction="completion").inc(int(completion))
        # 分位数只对成功应答有意义（用户真实等到的回复）
        if outcome == "success" and total_ms_v:
            latency_total_ms.observe(float(total_ms_v))
        if outcome == "success" and http_ms_v:
            http_ms.observe(float(http_ms_v))
    return (rows[-1][0] if rows else cursor), len(rows)


def poll_queue() -> None:
    import redis  # 局部导入：MySQL 不可达时不应连带 redis 指标失败

    client = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, socket_timeout=2
    )
    queue_depth.set(client.llen(QUEUE_KEY))


def main() -> None:
    start_http_server(LISTEN_PORT, addr=LISTEN_HOST)
    print(f"exporter listening on {LISTEN_HOST}:{LISTEN_PORT}/metrics", flush=True)

    conn = mysql_conn()
    with conn.cursor() as cur:
        cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {TABLE}")
        cursor = cur.fetchone()[0]
    rows_seen.set(cursor)
    print(f"starting from ai_call_log.id > {cursor}", flush=True)

    last_poll = 0.0
    while True:
        now = time.time()
        try:
            if now - last_poll >= SCRAPE_INTERVAL:
                last_poll = now
                conn.ping(reconnect=True)
                cursor, _n = poll_once(conn, cursor)
                rows_seen.set(cursor)
                poll_queue()
        except Exception as exc:  # noqa: BLE001 —— exporter 绝不退出，失败计数后重试
            scrape_errors_total.inc()
            print(f"poll error: {exc}", flush=True)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)
            try:
                conn = mysql_conn()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.2)


if __name__ == "__main__":
    main()
