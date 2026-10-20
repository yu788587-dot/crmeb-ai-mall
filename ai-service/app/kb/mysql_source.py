"""只读 MySQL 知识库源：商品、客服话术、关键词回复。

约定（窗口 3 PHP 侧与这里共同遵守）：
- 永远 SELECT，绝不 UPDATE/DELETE；连接用 read_only=True 的 pymysql 客户端；
- 表名带前缀 {p}（默认 eb_），避免硬编码；
- 字段缺失时给空串而不是抛异常，保证同步不会因为一条脏数据整批失败。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Sequence

import pymysql
from pymysql.cursors import DictCursor

from app.config import Settings
from app.rag.documents import (
    DOC_KEYWORD_REPLY,
    DOC_PRODUCT,
    DOC_SPEECHCRAFT,
    keyword_reply_document,
    product_document,
    speechcraft_document,
)

log = logging.getLogger("ai.kb")


class ReadOnlyMySQLSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._conn: pymysql.connections.Connection | None = None

    async def _ensure_conn(self) -> pymysql.connections.Connection:
        if self._conn is not None and self._conn.open:
            return self._conn
        self._conn = pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            connect_timeout=self.settings.mysql_connect_timeout,
            read_timeout=10,
            write_timeout=None,
            autocommit=True,
        )
        # pymysql 没有原生 read_only flag，靠 SQL 层拒绝写操作做兜底
        with self._conn.cursor() as cur:
            try:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            except Exception:
                log.warning("无法设置 READ ONLY 事务（MySQL 版本或权限不足），请在应用层严格只用 SELECT")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    async def _query(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        """所有 SQL 必须大写开头为 SELECT / SHOW / DESCRIBE / EXPLAIN，否则直接拒绝。"""
        head = sql.strip().split()[0].upper()
        if head not in {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN"}:
            raise PermissionError(f"只允许只读查询，收到 {head!r}")
        conn = await self._ensure_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    async def iter_products(self, *, limit: int) -> AsyncGenerator[dict[str, Any], None]:
        p = self.settings.table
        rows = await self._query(
            f"SELECT id, store_name, keyword, store_info, price, stock, unit_name, sales "
            f"FROM `{p}store_product` WHERE is_show=1 AND is_del=0 ORDER BY sort DESC, id DESC LIMIT %s",
            (limit,),
        )
        for row in rows:
            yield row

    async def iter_speechcraft(self) -> AsyncGenerator[dict[str, Any], None]:
        p = self.settings.table
        rows = await self._query(
            f"SELECT id, kefu_id, title, message FROM `{p}store_service_speechcraft` ORDER BY sort ASC, id ASC"
        )
        for row in rows:
            yield row

    async def iter_keyword_replies(self) -> AsyncGenerator[tuple[dict[str, Any], dict[str, Any]], None]:
        p = self.settings.table
        rows = await self._query(
            f"SELECT k.id, k.reply_id, k.`keys`, k.key_type, r.type, r.data, r.status "
            f"FROM `{p}wechat_key` k JOIN `{p}wechat_reply` r ON r.id=k.reply_id "
            f"WHERE r.status=1 AND r.hide=0"
        )
        for row in rows:
            key_row = {"id": row["id"], "reply_id": row["reply_id"], "keys": row["keys"], "key_type": row["key_type"]}
            reply_row = {"id": row["reply_id"], "type": row["type"], "data": row["data"], "status": row["status"]}
            yield key_row, reply_row

    async def snapshot_counts(self) -> dict[str, int]:
        p = self.settings.table
        rows = await self._query(
            f"SELECT "
            f"(SELECT COUNT(*) FROM `{p}store_product` WHERE is_show=1 AND is_del=0) AS products, "
            f"(SELECT COUNT(*) FROM `{p}store_service_speechcraft`) AS speechcraft, "
            f"(SELECT COUNT(*) FROM `{p}wechat_key` k JOIN `{p}wechat_reply` r ON r.id=k.reply_id WHERE r.status=1 AND r.hide=0) AS keyword_replies"
        )
        return rows[0] if rows else {"products": 0, "speechcraft": 0, "keyword_replies": 0}
