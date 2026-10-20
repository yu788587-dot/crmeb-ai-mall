"""运行期配置：全部来自环境变量 / ai-service/.env。

设计约定（窗口 3 PHP 侧依赖，改动需同步 docs 第 11 节）：
- 超时分层：connect 1s（快速判定服务不可达）/ request 15s（真实生成上限）/ sla 20s（整条链路预算）
- 重试只对「连接失败 + 5xx」开放一次，超时绝不重试 —— 否则最坏耗时翻倍且会把重复计费打给上游
- 降级不是异常：LLM 挂了要返回 code=1 + degraded 原因，让 PHP 决定用自己的话术兜底
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 服务本身 ----------
    app_name: str = "crmeb-ai-service"
    app_version: str = "1.0.0"
    env: str = Field("dev", description="dev / prod，仅影响日志详细程度")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # ---------- 模型提供方 ----------
    # fake = 零网络、确定性输出，供单测与离线演示；siliconflow = 真实调用
    llm_provider: str = Field("siliconflow", description="siliconflow | fake")
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_api_key: str = ""
    chat_model: str = "deepseek-ai/DeepSeek-V3"
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    chat_max_tokens: int = 600
    chat_temperature: float = 0.3

    # ---------- 离线提供方（fake）专用：故障注入 ----------
    fake_fail_mode: str = Field("none", description="none | timeout | connect | 5xx | 4xx")
    fake_latency_ms: float = 0.0

    # ---------- 分层超时 / 重试 / 熔断 ----------
    connect_timeout_seconds: float = 1.0
    request_timeout_seconds: float = 15.0
    sla_seconds: float = 20.0
    retry_max_attempts: int = Field(1, description="0=不重试，1=额外重试一次")
    retry_backoff_seconds: float = 0.2

    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 30.0
    breaker_half_open_max_calls: int = 2

    # ---------- 背压 ----------
    max_inflight: int = Field(8, description="同时处理的 LLM 请求数")
    max_queue: int = Field(32, description="等待配额的请求上限，超出直接 503")

    # ---------- 幂等 ----------
    idempotency_ttl_seconds: float = 600.0
    idempotency_max_entries: int = 4096

    # ---------- 知识库 / RAG ----------
    kb_enabled: bool = True
    kb_collection: str = "crmeb_kb"
    kb_dir: str = str(BASE_DIR / "data" / "chroma")
    kb_top_k: int = 4
    kb_min_score: float = 0.25
    kb_sync_limit_products: int = 500
    kb_sync_interval_seconds: float = Field(0.0, description="0=不自动同步，只走接口触发")

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 33061
    mysql_user: str = "crmeb"
    mysql_password: str = "123456"
    mysql_database: str = "crmeb"
    mysql_prefix: str = "eb_"
    mysql_connect_timeout: int = 3
    mysql_read_only: bool = Field(True, description="只读；写操作会在 sql() 层被拒绝")

    # ---------- 降级话术（PHP 也可自带，这里保证接口本身能给出可读兜底）----------
    fallback_text: str = "抱歉，当前智能客服暂时不可用，您可以换个说法再问一次，或等待人工客服回复。"
    no_answer_text: str = "这个问题我在商品和客服资料里没有找到依据，为避免误导，建议联系人工客服确认。"
    low_confidence_text: str = "参考资料里只有部分相关信息：{hint}\n如与实际不符，请以商品页与人工客服为准。"

    # ---------- 可观测 ----------
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"

    @property
    def table(self) -> str:
        """拼表名前缀，SQL 里用 {p} 占位。"""
        return self.mysql_prefix

    def masked(self) -> dict:
        """给 /api/health 与日志用的安全快照：不含任何密钥。"""
        return {
            "app": self.app_name,
            "version": self.app_version,
            "env": self.env,
            "provider": self.llm_provider,
            "chat_model": self.chat_model,
            "embed_model": self.embed_model,
            "fake_fail_mode": self.fake_fail_mode if self.llm_provider == "fake" else None,
            "timeouts": {
                "connect": self.connect_timeout_seconds,
                "request": self.request_timeout_seconds,
                "sla": self.sla_seconds,
            },
            "retry_max_attempts": self.retry_max_attempts,
            "breaker": {
                "failure_threshold": self.breaker_failure_threshold,
                "reset_seconds": self.breaker_reset_seconds,
            },
            "backpressure": {"max_inflight": self.max_inflight, "max_queue": self.max_queue},
            "kb": {
                "enabled": self.kb_enabled,
                "collection": self.kb_collection,
                "top_k": self.kb_top_k,
                "min_score": self.kb_min_score,
                "mysql_database": self.mysql_database,
                "mysql_read_only": self.mysql_read_only,
            },
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
