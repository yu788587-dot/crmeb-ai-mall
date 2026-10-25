-- ----------------------------------------------------------------------
-- AI 运行监控（crmeb-ai-mall 窗口4 可观测轨道一）
-- 1) eb_ai_call_log 埋点表：PHP 侧（AiChatService 派发 / AiChatJob 终态）每次 AI
--    处理写一行终态记录，是后台「AI 运行监控」页与指标 exporter 的唯一数据源；
--    结构化日志（[ai] ...）仍保留，两者内容一致、用途不同（日志排障 / 表聚合出数）。
-- 2) eb_system_menus 菜单行：后台「设置」分组下新增「AI 运行监控」入口。
--    菜单仅是数据行：后端 getMenusList 返回时自动加 /admin 前缀
--    （SystemMenusServices:55），与前端 router/modules/setting.js 注册的
--    /admin/setting/ai_monitor 对应；unique_auth 对应前端 meta.auth。
-- 幂等：可重复执行（表 IF NOT EXISTS，菜单按 unique_auth 判存）。
-- 执行后需清理配置缓存：DEL redis 中含 system_config 的键（同窗口3约定）。
-- 注意：含中文，导入必须显式字符集（否则菜单名双重编码乱码）：
--   docker exec -i crmeb_mysql mysql -uroot -p123456 --default-character-set=utf8mb4 crmeb < help/sql/ai_monitor.sql
-- ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `eb_ai_call_log` (
  `id`                int unsigned NOT NULL AUTO_INCREMENT,
  `trace_id`          varchar(32)  NOT NULL DEFAULT '' COMMENT '链路 id（ai-16hex），双端日志可串联',
  `event`             varchar(32)  NOT NULL DEFAULT '' COMMENT '事件：dispatch_backpressure/deadline_blown/upstream_failed/silent_degrade/fallback_sent/reply_sent/bad_envelope/empty_reply/job_error/dispatch_error',
  `outcome`           varchar(16)  NOT NULL DEFAULT '' COMMENT '归一化结果：success/silent/notify/rejected/error',
  `uid`               int unsigned NOT NULL DEFAULT 0 COMMENT '用户 uid',
  `kefu_uid`          int unsigned NOT NULL DEFAULT 0 COMMENT '客服 uid（AI 回复落库身份）',
  `msg_id`            int unsigned NOT NULL DEFAULT 0 COMMENT '用户消息 store_service_log.id（幂等键）',
  `http_status`       smallint     NOT NULL DEFAULT 0 COMMENT 'ai-service HTTP 状态码（0=未到达）',
  `attempts`          tinyint      NOT NULL DEFAULT 0 COMMENT 'HTTP 尝试次数（>1 即发生重试）',
  `http_ms`           decimal(10,1) NOT NULL DEFAULT 0 COMMENT 'HTTP 层耗时 ms（含重试）',
  `ai_ms`             decimal(10,1) NOT NULL DEFAULT 0 COMMENT 'ai-service 内部耗时 ms（envelope.latency_ms）',
  `total_ms`          decimal(10,1) NOT NULL DEFAULT 0 COMMENT 'job 全程耗时 ms（出队→落库推送）',
  `kb_used`           tinyint      NOT NULL DEFAULT 0 COMMENT 'RAG 是否命中知识库',
  `degraded`          tinyint      NOT NULL DEFAULT 0 COMMENT '是否降级应答',
  `reason`            varchar(32)  NOT NULL DEFAULT '' COMMENT '降级原因：no_sources/low_confidence/llm_timeout/circuit_open 等',
  `prompt_tokens`     int unsigned NOT NULL DEFAULT 0 COMMENT 'LLM 输入 token',
  `completion_tokens` int unsigned NOT NULL DEFAULT 0 COMMENT 'LLM 输出 token',
  `error`             varchar(255) NOT NULL DEFAULT '' COMMENT '错误摘要（curl 码/http 状态/异常消息）',
  `add_time`          int unsigned NOT NULL DEFAULT 0 COMMENT '记录时刻（unix 秒）',
  PRIMARY KEY (`id`),
  KEY `idx_add_time` (`add_time`),
  KEY `idx_trace` (`trace_id`),
  KEY `idx_event` (`event`, `outcome`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AI 调用埋点（可观测轨道一）';

-- 后台菜单：「设置」分组（pid=12）下新增「AI 运行监控」
-- menu_path 存储不含 /admin 前缀（SystemMenusServices 返回时统一拼接）；
-- unique_auth = admin-setting-ai_monitor 与前端路由 meta.auth 对应。
INSERT INTO `eb_system_menus`
  (`pid`, `icon`, `menu_name`, `module`, `controller`, `action`, `api_url`, `methods`, `params`,
   `sort`, `is_show`, `is_show_path`, `access`, `menu_path`, `path`, `auth_type`, `header`,
   `is_header`, `unique_auth`, `is_del`, `mark`)
SELECT 12, 'ios-pulse', 'AI运行监控', 'admin', '', '', '', '', '[]',
       5, 1, 1, 1, '/setting/ai_monitor', '12', 1, '',
       0, 'admin-setting-ai_monitor', 0, 'AI 智能客服运行监控（调用量/成功率/延迟/降级/trace 查询）'
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM `eb_system_menus` WHERE `unique_auth` = 'admin-setting-ai_monitor' AND `is_del` = 0);
