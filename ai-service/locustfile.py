"""AI 微服务直压 locust 场景（窗口4 量化）。

目标服务：慢速/快速 fake provider 实例（LLM_PROVIDER=fake），零外部依赖、确定性延迟。
两个用法（见窗口4文档）：
  1. 吞吐上限：FAKE_LATENCY_MS=0 的实例，用户少（8 并发即可打满），测 RPS 与分位数
  2. 背压边界：FAKE_LATENCY_MS=2500 的实例，并发爬坡 > max_inflight+max_queue（8+32=40）
     → 观测 503 拒绝出现的位置与响应延迟的退化

运行（在 ai-service 目录）：
  .venv/Scripts/python -m locust -f locustfile.py --host http://127.0.0.1:8010 \
      --headless -u 20 -r 5 -t 60s --csv results/direct --only-summary
"""

import json
import random

from locust import HttpUser, between, task

MSGS = [
    "请问这款测试手机多少钱？有现货吗？",
    "蓝牙耳机是什么价格？",
    "你们几点发货？支持什么快递？",
    "退换货政策是什么？",
    "介绍一下你们的会员优惠。",
]


class AiChatUser(HttpUser):
    wait_time = between(0, 0.05)

    @task
    def chat(self):
        payload = {
            "user_msg_id": str(random.randint(10**12, 10**13)),  # 随机幂等键：每次都是新请求
            "uid": random.randint(100, 200),
            "kefu_uid": 1,
            "message": random.choice(MSGS),
            "history": [],
            "kb_enabled": True,
            "deadline_ms": 20000,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Trace-Id": f"load-{random.randint(10**8, 10**9)}",
        }
        with self.client.post(
            "/api/chat", data=json.dumps(payload), headers=headers, catch_response=True
        ) as resp:
            if resp.status_code == 503:
                resp.mark_success()  # 503=背压拒绝也是有效观测点，不算压测失败
                resp.request_meta["response_length"] = 0
            elif resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
