"""知识库文档模型：把 CRMEB 三张业务表统一成「可检索的一条知识」。

规范文本（embedding 用）与展示文本（进提示词/后台溯源用）分开，
避免为了向量效果而污染给模型看的内容，也避免反过来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DOC_PRODUCT = "product"
DOC_SPEECHCRAFT = "speechcraft"
DOC_KEYWORD_REPLY = "keyword_reply"

DOC_TYPE_LABELS = {
    DOC_PRODUCT: "商品",
    DOC_SPEECHCRAFT: "客服话术",
    DOC_KEYWORD_REPLY: "关键词回复",
}


@dataclass
class KBDocument:
    doc_type: str
    source_id: int
    title: str
    embed_text: str
    context_line: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return f"{self.doc_type}:{self.source_id}"

    def to_chroma(self) -> tuple[str, str, dict[str, Any]]:
        meta = {"doc_type": self.doc_type, "source_id": self.source_id, "title": self.title[:120]}
        meta.update({f"p_{k}": v for k, v in self.payload.items() if isinstance(v, (int, float, str, bool))})
        return self.doc_id, self.embed_text, meta


@dataclass
class Hit:
    document: KBDocument
    score: float

    def as_source(self) -> dict[str, Any]:
        return {
            "type": self.document.doc_type,
            "type_label": DOC_TYPE_LABELS.get(self.document.doc_type, self.document.doc_type),
            "id": self.document.source_id,
            "title": self.document.title,
            "score": round(self.score, 4),
        }


def product_document(row: dict[str, Any]) -> KBDocument:
    """商品：把价格、库存、单位这些「模型最容易答错的数字」写进规范文本，让检索能命中、生成能引用。"""
    name = (row.get("store_name") or "").strip()
    keyword = (row.get("keyword") or "").strip()
    info = (row.get("store_info") or "").strip()
    price = row.get("price")
    stock = row.get("stock")
    unit = (row.get("unit_name") or "件").strip()
    parts = [f"商品名称：{name}"]
    if keyword:
        parts.append(f"关键词/搜索词：{keyword}")
    if info:
        parts.append(f"简介：{info}")
    parts.append(f"售价：{price} 元/{unit}")
    parts.append(f"库存：{stock}")
    sales = row.get("sales")
    if sales is not None:
        parts.append(f"累计销量：{sales}")
    return KBDocument(
        doc_type=DOC_PRODUCT,
        source_id=int(row["id"]),
        title=name,
        embed_text="；".join(parts),
        context_line=f"[商品#{row['id']}] {name}｜{price} 元/{unit}｜库存 {stock}" + (f"｜{info}" if info else ""),
        payload={"price": str(price), "stock": int(stock or 0), "unit": unit, "sales": int(sales or 0)},
    )


def speechcraft_document(row: dict[str, Any]) -> KBDocument:
    title = (row.get("title") or "").strip()
    message = (row.get("message") or "").strip()
    return KBDocument(
        doc_type=DOC_SPEECHCRAFT,
        source_id=int(row["id"]),
        title=title,
        embed_text=f"客服话术：{title}；内容：{message}",
        context_line=f"[话术#{row['id']}] {title}：{message}",
        payload={"kefu_id": int(row.get("kefu_id") or 0)},
    )


def keyword_reply_document(key_row: dict[str, Any], reply_row: dict[str, Any]) -> KBDocument:
    """关键词自动回复：这是原版「机器人」的规则，AI 把它当作问答对来学习而不是当规则执行。"""
    keys = (key_row.get("keys") or "").strip()
    raw = (reply_row.get("data") or "").strip()
    answer = extract_reply_text(raw, str(reply_row.get("type") or ""))
    return KBDocument(
        doc_type=DOC_KEYWORD_REPLY,
        source_id=int(key_row["id"]),
        title=keys,
        embed_text=f"用户常问：{keys}；官方回复：{answer}",
        context_line=f"[关键词回复#{key_row['id']}] 触发词「{keys}」→ {answer}",
        payload={"key_type": int(key_row.get("key_type") or 0), "reply_type": str(reply_row.get("type") or "")},
    )


def extract_reply_text(data: str, reply_type: str) -> str:
    """eb_wechat_reply.data 是 JSON（{"content":"..."}），也可能是多行 markdown/图片链接，做统一清洗。"""
    import json

    if not data:
        return ""
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return data[:200]
    if isinstance(parsed, dict):
        for key in ("content", "text", "reply", "msg"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
        return json.dumps(parsed, ensure_ascii=False)[:300]
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        return str(first.get("content") if isinstance(first, dict) else first)[:300]
    return str(parsed)[:300]
