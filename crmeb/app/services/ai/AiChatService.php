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

use app\jobs\AiChatJob;
use app\services\ai\AiCallLog;
use crmeb\utils\Queue;
use think\facade\Env;
use think\facade\Log;

/**
 * AI 智能客服派发服务（窗口3）
 *
 * ChatHandle 在「关键词未命中 && 对话客服离线」后调用 tryDispatch；
 * 这里完成：开关校验 → 消息级幂等锁 → 队列背压检查 → 生成 trace_id → 投递 AiChatJob。
 *
 * 约束：任何异常都必须吞掉并返回 false——AI 是可降级增强，
 * 绝不允许影响原聊天链路（对应方案第三节「AI 故障 → 静默降级」）。
 */
class AiChatService
{
    /**
     * 消息级幂等锁前缀：一条用户消息（store_service_log.id）最多触发一次 AI 回复。
     * 队列重投、job 重试、并发双发都在这里被拦下；与 ai-service 端的 user_msg_id 幂等双保险。
     */
    const REPLY_LOCK_PREFIX = 'ai:reply_lock:';

    /** 幂等锁 TTL：86400s，过期后同一条消息也不会再触发（有 ai-service 侧 TTL 兜底） */
    const REPLY_LOCK_TTL = 86400;

    /**
     * 尝试为一条客服消息派发 AI 回复任务
     *
     * @param array $msg store_service_log 落库后的消息行（含 id/uid/to_uid/msn/msn_type/add_time）
     * @param int $formType 客户端来源，写回 store_service_record 需要
     * @param bool $isTourist 是否游客（游客无 uid 上下文，不接管）
     * @return bool 是否成功入队
     */
    public function tryDispatch(array $msg, int $formType, bool $isTourist): bool
    {
        try {
            if (sys_config('ai_service_open', 0) != 1) {
                return false;
            }
            if ($isTourist) {
                return false;
            }
            if ((int)($msg['msn_type'] ?? 0) !== 1) {
                //仅文本消息走 AI，图片/商品/订单卡片不生成回复
                return false;
            }
            $msgId = (int)($msg['id'] ?? 0);
            if ($msgId <= 0) {
                return false;
            }

            $redis = $this->redis();
            if ($redis && !$redis->setNx(self::REPLY_LOCK_PREFIX . $msgId, 1)) {
                return false;
            }
                if ($redis) {
                    $redis->expire(self::REPLY_LOCK_PREFIX . $msgId, self::REPLY_LOCK_TTL);

                    //背压：待处理任务数超上限直接放弃，AI 不排队堆积（超限降级）
                    //键名与 think-queue redis 驱动一致：queues:{queue}（Redis.php getQueue）
                    $queueKey = 'queues:' . (string)config('queue.connections.redis.queue', 'CRMEB');
                    $limit = (int)sys_config('ai_service_queue_limit', 50);
                    $pending = (int)$redis->lLen($queueKey);
                    if ($pending > $limit) {
                    Log::warning('[ai] dispatch_backpressure ' . json_encode([
                        'pending'   => $pending,
                        'limit'     => $limit,
                        'msg_id'    => $msgId,
                    ], JSON_UNESCAPED_UNICODE));
                    //埋点：派发被背压拒绝，消息未入队（job 侧不会有记录）
                    AiCallLog::record([
                        'msg_id'   => $msgId,
                        'uid'      => (int)$msg['uid'],
                        'kefu_uid' => (int)$msg['to_uid'],
                        'event'    => 'dispatch_backpressure',
                        'outcome'  => AiCallLog::OUTCOME_REJECTED,
                        'error'    => 'pending:' . $pending . '>limit:' . $limit,
                    ]);
                    return false;
                }
            }

            //trace_id 在 PHP 侧生成，经队列任务 → HTTP 头 X-Trace-Id → ai-service 日志，双端可串联
            $traceId = 'ai-' . bin2hex(random_bytes(8));
            $res = Queue::instance()->job(AiChatJob::class)->do('doJob')->data(
                $msgId,
                (int)$msg['uid'],
                (int)$msg['to_uid'],
                (string)$msg['msn'],
                $formType,
                $traceId,
                (int)($msg['add_time'] ?? 0)
            )->push();

            Log::info('[ai] dispatch ' . json_encode([
                'trace_id' => $traceId,
                'msg_id'   => $msgId,
                'uid'      => (int)$msg['uid'],
                'kefu_uid' => (int)$msg['to_uid'],
                'queued'   => (bool)$res,
            ], JSON_UNESCAPED_UNICODE));
            return (bool)$res;
        } catch (\Throwable $e) {
            Log::error('[ai] dispatch_error ' . json_encode([
                'msg_id' => (int)($msg['id'] ?? 0),
                'error'  => $e->getMessage(),
            ], JSON_UNESCAPED_UNICODE));
            AiCallLog::record([
                'msg_id'   => (int)($msg['id'] ?? 0),
                'uid'      => (int)($msg['uid'] ?? 0),
                'kefu_uid' => (int)($msg['to_uid'] ?? 0),
                'event'    => 'dispatch_error',
                'outcome'  => AiCallLog::OUTCOME_ERROR,
                'error'    => $e->getMessage(),
            ]);
            return false;
        }
    }

    /**
     * 原生 redis 连接（按 .env [REDIS] 直连，进程内复用）。
     * 不走 Cache::handler()：本部署 cache.driver=file，拿不到 \Redis 句柄，
     * 而幂等锁与背压检查依赖 list 操作，必须直连（与 think-queue 的连接方式一致）。
     * 连接失败返回 null，幂等与背压检查随之跳过——队列本身也依赖 redis，
     * 此时入队同样会失败，行为一致。
     * @return \Redis|null
     */
    protected function redis()
    {
        static $redis = null;
        static $failed = false;
        if ($failed) {
            return null;
        }
        try {
            if ($redis === null) {
                $redis = new \Redis();
                $ok = $redis->connect(
                    (string)Env::get('redis.redis_hostname', '127.0.0.1'),
                    (int)Env::get('redis.port', 6379),
                    3
                );
                if (!$ok) {
                    return null;
                }
                $password = (string)Env::get('redis.redis_password', '');
                if ($password !== '' && !$redis->auth($password)) {
                    return null;
                }
                $select = Env::get('redis.select', false);
                if ($select !== false && !$redis->select((int)$select)) {
                    return null;
                }
            }
            return $redis;
        } catch (\Throwable $e) {
            $failed = true;
            return null;
        }
    }
}
