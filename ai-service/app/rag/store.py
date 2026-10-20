"""Chroma 向量库封装（cosine 空间）。

几点刻意的设计决定：
1. 遥测关闭：chromadb 默认上报匿名使用数据，开源项目里必须显式关掉；
   chroma 0.5.23 + posthog 7.x 有签名不兼容 bug，光设 anonymized_telemetry=False
   仍会触发 "capture() takes 1 positional argument but 3 were given" 警告，
   所以这里把 product telemetry impl 替换成 app._chroma_noop.NoopProductTelemetry。
2. 自己算 embedding：不用 chroma 内置 embedding_function，向量统一由 SiliconFlow bge-m3 产出，
   换模型时只需换提供方，不用担心 chroma 偷偷加载本地 ONNX 模型；
3. 全部同步调用走 asyncio.to_thread：chroma 是同步库，直接在协程里调会卡住事件循环，
   进而拖垮「同一会话串行」和超时预算。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Iterable, Sequence

from app.rag.documents import DOC_TYPE_LABELS, KBDocument, Hit

# 双保险：环境变量 + Settings 字段都禁用，避免任何路径绕过
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_ANONYMIZED_TELEMETRY", "False")

log = logging.getLogger("ai.rag")


class KnowledgeStore:
    def __init__(self, *, path: str, collection: str, dim: int) -> None:
        self.path = path
        self.collection_name = collection
        self.dim = dim
        self._client = None
        self._collection = None

    # ---------------- 内部 ----------------

    def _ensure(self):
        if self._collection is None:
            import chromadb  # 延迟导入：单测里不碰磁盘时不需要它
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=self.path,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                    chroma_product_telemetry_impl="app._chroma_noop.NoopProductTelemetry",
                ),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},  # distance = 1 - cos，检索分数直接可比
                embedding_function=None,  # 关键：禁掉内置 ONNX MiniLM，向量一律由外部 provider 提供
            )
        return self._collection

    @staticmethod
    def _to_hit(metadata: dict[str, Any], distance: float) -> Hit:
        doc = KBDocument(
            doc_type=str(metadata.get("doc_type", "")),
            source_id=int(metadata.get("source_id", 0)),
            title=str(metadata.get("title", "")),
            embed_text=str(metadata.get("embed_text", "")),
            context_line=str(metadata.get("context_line", "")),
            payload={k[2:]: v for k, v in metadata.items() if k.startswith("p_")},
        )
        # cosine 空间下 chroma 返回 distance = 1 - cos，相似度 = 1 - distance
        return Hit(document=doc, score=1.0 - float(distance))

    # ---------------- 对外（均为协程） ----------------

    async def upsert(self, docs: Sequence[KBDocument], vectors: Sequence[Sequence[float]]) -> int:
        if not docs:
            return 0
        if len(docs) != len(vectors):
            raise ValueError(f"docs={len(docs)} 与 vectors={len(vectors)} 不匹配")

        def work() -> int:
            col = self._ensure()
            col.upsert(
                ids=[d.doc_id for d in docs],
                embeddings=[list(map(float, v)) for v in vectors],
                metadatas=[
                    {
                        **d.to_chroma()[2],
                        "context_line": d.context_line[:500],
                        "embed_text": d.embed_text[:500],
                    }
                    for d in docs
                ],
                documents=[d.embed_text for d in docs],
            )
            return len(docs)

        return await asyncio.to_thread(work)

    async def search(self, vector: Sequence[float], *, top_k: int, min_score: float) -> list[Hit]:
        def work() -> list[Hit]:
            col = self._ensure()
            total = col.count()
            if total == 0:
                return []
            res = col.query(
                query_embeddings=[[float(x) for x in vector]],
                n_results=min(top_k, total) if total else top_k,
                include=["metadatas", "distances"],
            )
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            return [self._to_hit(m, d) for m, d in zip(metas, dists)]

        hits = await asyncio.to_thread(work)
        return [h for h in hits if h.score >= min_score]

    async def count(self) -> int:
        return await asyncio.to_thread(lambda: self._ensure().count())

    async def stats_by_type(self) -> dict[str, int]:
        def work() -> dict[str, int]:
            col = self._ensure()
            data = col.get(include=["metadatas"])
            out: dict[str, int] = {}
            for meta in data.get("metadatas") or []:
                key = str(meta.get("doc_type", "unknown"))
                out[key] = out.get(key, 0) + 1
            return out

        return await asyncio.to_thread(work)

    async def reset(self) -> int:
        """整库重建（知识库是 MySQL 的只读派生物，重建比增量删除更可靠）。返回删除前条数。"""

        def work() -> int:
            col = self._ensure()
            removed = col.count()
            self._client.delete_collection(self.collection_name)
            self._collection = None
            return removed

        return await asyncio.to_thread(work)

    async def describe(self) -> dict[str, Any]:
        count = await self.count()
        by_type = await self.stats_by_type()
        return {
            "collection": self.collection_name,
            "path": self.path,
            "dim": self.dim,
            "documents": count,
            "by_type": {DOC_TYPE_LABELS.get(k, k): v for k, v in by_type.items()},
        }


def iter_batches(items: Iterable[Any], size: int):
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
