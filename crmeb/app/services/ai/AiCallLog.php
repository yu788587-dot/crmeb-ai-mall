<?php
// +----------------------------------------------------------------------
// | CRMEB [ CRMEB赋能开发者，助力企业发展 ]
// +----------------------------------------------------------------------
// | Copyright (c) 2016~2026 https://www.crmeb.com All rights reserved.
// +----------------------------------------------------------------------
// | Licensed under the Apache License 2.0 （基于 CRMEB-KY v6.0.0 二次开发，修改以 git 历史体现）
// +----------------------------------------------------------------------
// | Author: CRMEB Team <admin@crmeb.com>
// +----------------------------------------------------------------------

namespace app\services\ai;

use think\facade\Db;
use think\facade\Log;

/**
 * AI 调用埋点（窗口4 可观测轨道一）
 *
 * 每次会话式 AI 处理写一行终态记录到 eb_ai_call_log：
 *  - 派发侧（AiChatService）：dispatch_backpressure / dispatch_error
 *  - 任务侧（AiChatJob）：reply_sent / silent_degrade / fallback_sent /
 *    upstream_failed / bad_envelope / empty_reply / deadline_blown / job_error
 *
 * 与结构化日志（[ai] ...）字段一致：日志用于排障，本表用于聚合出数
 * （后台「AI 运行监控」页 + 指标 exporter 均只读这张表）。
 *
 * 契约：写表失败绝不影响 AI 主链路——吞异常仅记 error 日志。
 */
class AiCallLog
{
    /** 归一化结果：成功（含软降级，degraded=1 区分） */
    const OUTCOME_SUCCESS = 'success';
    /** 硬故障静默（notify_on_failure=0 时用户零感知） */
    const OUTCOME_SILENT = 'silent';
    /** 故障发送了降级话术 */
    const OUTCOME_NOTIFY = 'notify';
    /** 派发被拒（背压/异常），消息未入队 */
    const OUTCOME_REJECTED = 'rejected';
    /** 任务内部异常 */
    const OUTCOME_ERROR = 'error';

    /**
     * 写一行埋点记录
     *
     * @param array $fields 支持 trace_id/event/outcome/uid/kefu_uid/msg_id/http_status/
     *                      attempts/http_ms/ai_ms/total_ms/kb_used/degraded/reason/
     *                      prompt_tokens/completion_tokens/error
     */
    public static function record(array $fields): void
    {
        try {
            Db::name('ai_call_log')->insert([
                'trace_id'          => substr((string)($fields['trace_id'] ?? ''), 0, 32),
                'event'             => substr((string)($fields['event'] ?? ''), 0, 32),
                'outcome'           => substr((string)($fields['outcome'] ?? ''), 0, 16),
                'uid'               => (int)($fields['uid'] ?? 0),
                'kefu_uid'          => (int)($fields['kefu_uid'] ?? 0),
                'msg_id'            => (int)($fields['msg_id'] ?? 0),
                'http_status'       => (int)($fields['http_status'] ?? 0),
                'attempts'          => (int)($fields['attempts'] ?? 0),
                'http_ms'           => round((float)($fields['http_ms'] ?? 0), 1),
                'ai_ms'             => round((float)($fields['ai_ms'] ?? 0), 1),
                'total_ms'          => round((float)($fields['total_ms'] ?? 0), 1),
                'kb_used'           => (int)!!($fields['kb_used'] ?? 0),
                'degraded'          => (int)!!($fields['degraded'] ?? 0),
                'reason'            => substr((string)($fields['reason'] ?? ''), 0, 32),
                'prompt_tokens'     => (int)($fields['prompt_tokens'] ?? 0),
                'completion_tokens' => (int)($fields['completion_tokens'] ?? 0),
                'error'             => mb_substr((string)($fields['error'] ?? ''), 0, 255),
                'add_time'          => time(),
            ]);
        } catch (\Throwable $e) {
            //埋点只增不减，任何写表异常都不允许影响 AI/聊天主链路
            Log::error('[ai] call_log_error ' . json_encode(['error' => $e->getMessage()], JSON_UNESCAPED_UNICODE));
        }
    }
}
