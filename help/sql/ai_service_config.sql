-- ----------------------------------------------------------------------
-- AI 智能客服配置分组（crmeb-ai-mall 窗口3）
-- 低代码落地：仅插入 system_config_tab + system_config 数据行，
-- 后台「设置」表单由 FormBuilder 依这些行自动渲染，无需新增 Vue 页面。
-- 幂等：可重复执行（eng_title / menu_name 已存在则跳过）。
-- 执行后需清理配置缓存：DEL redis 中含 system_config 的键（见窗口3文档）。
-- ----------------------------------------------------------------------

-- 1) 配置分组（顶层，与「客服配置」平级）
INSERT INTO `eb_system_config_tab` (`pid`, `title`, `eng_title`, `status`, `info`, `icon`, `type`, `sort`, `menus_id`)
SELECT 0, 'AI 智能客服', 'ai_service', 1, 0, 'ios-robot', 3, 89, 0
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config_tab` WHERE `eng_title` = 'ai_service');

SET @ai_tab_id = (SELECT `id` FROM `eb_system_config_tab` WHERE `eng_title` = 'ai_service' LIMIT 1);

-- 2) 配置项
INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_open', 'radio', 'input', @ai_tab_id, '1=>开启\n0=>关闭', 1, '', 0, 0, '1', 'AI 智能客服开关', '客服离线且关键词未命中时由 AI 接管回复', 100, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_open');

INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_url', 'input', 'input', @ai_tab_id, '', 1, '', 100, 0, '"http://host.docker.internal:8000"', 'AI 服务地址', 'PHP 容器访问 ai-service 的基础地址（FastAPI）', 95, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_url');

INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_timeout', 'input', 'number', @ai_tab_id, '', 1, '', 0, 0, '20', '整体超时时间(秒)', '从用户发送消息起算的整体 SLA，超时放弃 AI 回复（超时不重试）', 90, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_timeout');

INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_history_limit', 'input', 'number', @ai_tab_id, '', 1, '', 0, 0, '6', '上下文携带条数', '调用 AI 时携带的最近文本聊天条数，0 为不携带', 85, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_history_limit');

INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_queue_limit', 'input', 'number', @ai_tab_id, '', 1, '', 0, 0, '50', '队列积压上限', '待处理队列任务数超过该值时暂停 AI 接管（背压降级，人工不受影响）', 80, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_queue_limit');

INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_notify_on_failure', 'radio', 'input', @ai_tab_id, '1=>开启\n0=>关闭', 1, '', 0, 0, '0', 'AI 故障时发送降级话术', '关闭=故障时静默不发、等待人工（推荐，原聊天功能零影响）；开启=发送下方降级话术', 75, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_notify_on_failure');

-- 注意：value 一律按 JSON 解析（SystemConfigServices::getConfigValue 做 json_decode），
-- 字符串值必须带双引号存储，数字值裸存即可。
INSERT INTO `eb_system_config` (`menu_name`, `type`, `input_type`, `config_tab_id`, `parameter`, `upload_type`, `required`, `width`, `high`, `value`, `info`, `desc`, `sort`, `status`)
SELECT 'ai_service_fallback_text', 'textarea', 'input', @ai_tab_id, '', 1, '', 100, 60, '"抱歉，智能客服暂时开小差了，请稍后再试或联系人工客服～"', '故障降级话术', '仅在「AI 故障时发送降级话术」开启时发送给用户', 70, 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `eb_system_config` WHERE `menu_name` = 'ai_service_fallback_text');
