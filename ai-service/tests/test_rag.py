"""RAG 文档模型与向量库基础测试。"""

import pytest

from app.providers.fake import FakeProvider, feature_hash_vector
from app.rag.documents import product_document, speechcraft_document, keyword_reply_document
from app.rag.store import KnowledgeStore


def test_feature_hash_deterministic():
    v1 = feature_hash_vector("测试手机", 64)
    v2 = feature_hash_vector("测试手机", 64)
    assert v1 == v2
    assert len(v1) == 64


def test_feature_hash_normalized():
    v = feature_hash_vector("任意文本内容", 64)
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6 or norm == 0.0


def test_product_document_context_line():
    doc = product_document({
        "id": 1, "store_name": "Xiaomi Civi 2", "keyword": "小米",
        "store_info": "轻薄旗舰", "price": 2299.0, "stock": 400,
        "unit_name": "台", "sales": 12,
    })
    assert doc.doc_type == "product"
    assert "2299" in doc.context_line
    assert "Xiaomi" in doc.context_line


@pytest.mark.asyncio
async def test_store_upsert_and_search(tmp_chroma_path):
    store = KnowledgeStore(path=tmp_chroma_path, collection="t_doc", dim=32)
    p = FakeProvider(embed_dim=32)
    docs = [
        product_document({"id": 1, "store_name": "A手机", "keyword": "", "store_info": "", "price": 100, "stock": 1, "unit_name": "台", "sales": 0}),
        product_document({"id": 2, "store_name": "B耳机", "keyword": "", "store_info": "", "price": 50, "stock": 10, "unit_name": "副", "sales": 0}),
    ]
    vecs = await p.embed([d.embed_text for d in docs])
    n = await store.upsert(docs, vecs)
    assert n == 2
    assert await store.count() == 2

    qv = await p.embed(["手机"])
    hits = await store.search(qv[0], top_k=2, min_score=-1.0)
    assert len(hits) > 0
    # A手机 应比 B耳机 更相关（词面重叠）
    titles = [h.document.title for h in hits]
    assert "A手机" in titles
