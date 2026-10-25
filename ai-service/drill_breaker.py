"""熔断器状态机演练（窗口4 故障演练）。

流程：
  1. :8010 慢速 fake 实例重启为 FAKE_FAIL_MODE=5xx（LLM 层持续失败）；
  2. 本脚本连续调用 /api/chat，观察应答从 success → degraded(circuit_open) 的时间点
     （连续失败达 BREAKER_FAILURE_THRESHOLD=5 次后开熔断）；
  3. 熔断打开后 FAIL 仍持续 → 记录 open 期间拒绝的延迟（应为毫秒级快速失败）；
  4. 等待 BREAKER_RESET_SECONDS=30s 后发送探测请求（half_open → closed 恢复路径）
     —— 恢复验证需要故障解除，脚本中途不改 .env，由外层 shell 控制：
     先跑 --phase fail（5 次失败触发熔断+持续秒级拒绝），
     实例切回 FAKE_LATENCY_MS（故障解除）后再跑 --phase recover（应答成功）。

输出：每个请求一行 T+秒数 / 状态 / reason / 耗时，供文档引用量化时序。
"""

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8010"
phase = sys.argv[1] if len(sys.argv) > 1 else "fail"

client = httpx.Client(timeout=25)
start = time.monotonic()

def one(i: int) -> dict:
    payload = {
        "user_msg_id": f"drill-{phase}-{i}",
        "uid": 900 + i,
        "kefu_uid": 1,
        "message": "测试手机多少钱？",
        "history": [],
        "kb_enabled": True,
        "deadline_ms": 20000,
    }
    t0 = time.monotonic()
    try:
        r = client.post(BASE + "/api/chat", json=payload, headers={"X-Trace-Id": f"drill-{phase}-{i}"})
        ms = (time.monotonic() - t0) * 1000
        body = r.json()
        data = body.get("data") or {}
        return {
            "i": i, "t": round(time.monotonic() - start, 1), "ms": round(ms, 1),
            "http": r.status_code, "degraded": bool(data.get("degraded")),
            "reason": data.get("reason") or "-", "kb": data.get("kb_used"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"i": i, "t": round(time.monotonic() - start, 1), "ms": round((time.monotonic() - t0) * 1000, 1),
                "http": 0, "degraded": True, "reason": f"transport:{exc}", "kb": None}

if phase == "fail":
    print("== 连发 12 次（前 5 次应触发熔断，之后应秒级 circuit_open 拒绝） ==")
    for i in range(12):
        r = one(i)
        print(f"T+{r['t']:>5}s  #{i+1:<3} http={r['http']}  {r['ms']:>8.1f}ms  degraded={r['degraded']}  reason={r['reason']}")
    h = client.get(BASE + "/api/health").json()
    print("breaker:", json.dumps(h["breaker"]))
elif phase == "recover":
    print("== 故障解除后：第 1-2 次探测应成功并恢复 closed ==")
    for i in range(3):
        r = one(i)
        print(f"T+{r['t']:>5}s  #{i+1:<3} http={r['http']}  {r['ms']:>8.1f}ms  degraded={r['degraded']}  reason={r['reason']}  kb={r['kb']}")
    h = client.get(BASE + "/api/health").json()
    print("breaker:", json.dumps(h["breaker"]))
