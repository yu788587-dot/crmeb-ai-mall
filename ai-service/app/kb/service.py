"""知识库同步服务：MySQL → embedding → Chroma。

设计要点：
- 整库重建（reset + upsert）而不是增量 diff：CRMEB 商品/话术体量小（几百条），
  全量重建比「逐条比对+部分更新」简单一个数量级，且不会留下脏数据；
- 批量 embedding：一次最多 32 条，避免单次请求 payload 过大被上游限流；
- 全部指标写入 metrics.kb_documents / kb_last_sync_timestamp，后台监控页直接读。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app import metrics
from app.config import Settings
from app.kb.mysql_source import ReadOnlyMySQLSource
from app.llm import ResilientLLM
from app.rag.documents import (
    DOC_KEYWORD_REPLY,
    DOC_PRODUCT,
    DOC_SPEECHCRAFT,
    keyword_reply_document,
    product_document,
    speechcraft_document,
)
from app.rag.store import KnowledgeStore, iter_batches

log = logging.getLogger("ai.kb")

EMBED_BATCH_SIZE = 32


class KBSyncService:
    def __init__(self, settings: Settings, source: ReadOnlyMySQLSource, store: KnowledgeStore, llm: ResilientLLM) -> None:
        self.settings = settings
        self.source = source
        self.store = store
        self.llm = llm

    async def sync(self, *, reset: bool = True) -> dict[str, Any]:
        started = time.perf_counter()
        docs_by_type: dict[str, int] = {DOC_PRODUCT: 0, DOC_SPEECHCRAFT: 0, DOC_KEYWORD_REPLY: 0}
        total_docs = 0

        if reset:
            removed = await self.store.reset()
            log.info("kb_reset", extra={"removed": removed})

        # 1. 商品
        batch_docs = []
        async for row in self.source.iter_products(limit=self.settings.kb_sync_limit_products):
            batch_docs.append(product_document(row))
            if len(batch_docs) >= EMBED_BATCH_SIZE:
                await self._upsert_batch(batch_docs)
                docs_by_type[DOC_PRODUCT] += len(batch_docs)
                total_docs += len(batch_docs)
                batch_docs = []
        if batch_docs:
            await self._upsert_batch(batch_docs)
            docs_by_type[DOC_PRODUCT] += len(batch_docs)
            total_docs += len(batch_docs)

        # 2. 客服话术
        batch_docs = []
        async for row in self.source.iter_speechcraft():
            batch_docs.append(speechcraft_document(row))
            if len(batch_docs) >= EMBED_BATCH_SIZE:
                await self._upsert_batch(batch_docs)
                docs_by_type[DOC_SPEECHCRAFT] += len(batch_docs)
                total_docs += len(batch_docs)
                batch_docs = []
        if batch_docs:
            await self._upsert_batch(batch_docs)
            docs_by_type[DOC_SPEECHCRAFT] += len(batch_docs)
            total_docs += len(batch_docs)

        # 3. 关键词回复
        batch_docs = []
        async for key_row, reply_row in self.source.iter_keyword_replies():
            batch_docs.append(keyword_reply_document(key_row, reply_row))
            if len(batch_docs) >= EMBED_BATCH_SIZE:
                await self._upsert_batch(batch_docs)
                docs_by_type[DOC_KEYWORD_REPLY] += len(batch_docs)
                total_docs += len(batch_docs)
                batch_docs = []
        if batch_docs:
            await self._upsert_batch(batch_docs)
            docs_by_type[DOC_KEYWORD_REPLY] += len(batch_docs)
            total_docs += len(batch_docs)

        elapsed = time.perf_counter() - started
        metrics.kb_last_sync_timestamp.set(time.time())
        for doc_type, count in docs_by_type.items():
            metrics.kb_documents.labels(doc_type=doc_type).set(count)

        result = {
            "ok": True,
            "elapsed_seconds": round(elapsed, 3),
            "total_docs": total_docs,
            "by_type": docs_by_type,
            "collection": self.store.collection_name,
        }
        log.info("kb_sync_completed", extra=result)
        return result

    async def _upsert_batch(self, docs) -> None:
        texts = [d.embed_text for d in docs]
        vectors = await self.llm.embed(texts)
        await self.store.upsert(docs, vectors)
