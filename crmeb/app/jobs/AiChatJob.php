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

namespace app\jobs;

use app\services\kefu\service\StoreServiceLogServices;
use app\services\kefu\service\StoreServiceRecordServices;
use app\services\kefu\service\StoreServiceServices;
use crmeb\basic\BaseJobs;
use crmeb\services\ai\AiClient;
use crmeb\services\workerman\ChannelService;
use think\facade\Db;
use think\facade\Log;

/**
 * AI 智能客服异步任务（窗口3）
 *
 * 进程链路：chat 进程(Workerman 40002) 投递 → queue 进程执行本 job
 *          → HTTP(X-Trace-Id) → ai-service /api/chat → 落库 store_service_log
 *          → ChannelService(40003, trigger=crmeb_chat) → chat 进程 ws 推送 reply
 *
 * 可靠性契约（对应方案第三节）：
 *  - 整体 SLA：按消息发送时刻计算剩余 deadline，超时前放弃（返回静默）
 *  - 幂等：user_msg_id = store_service_log 主键；PHP 侧 redis 锁 + ai-service 侧 TTL 缓存双保险
 *  - 降级：硬故障静默不发（或按配置发降级话术）；软降级（无资料/低置信）发送引导话术
 *  - 隔离：本 job 任何异常都不重投、不上抛——AI 失败等于没有 AI，原聊天功能零影响
 */
class AiChatJob extends BaseJobs
{
    /**
     * 硬故障：ai-service 基础设施问题，静默放弃（默认），
     * 用户无感知、等人工接管；后台开启 ai_service_notify_on_failure 后才发降级话术
     */
    const HARD_FAIL_REASONS = ['llm_timeout', 'llm_unavailable', 'circuit_open', 'overloaded', 'bad_upstream_response'];

    /**
     * 软降级：模型答不好（无资料/低置信），ai-service 已把 reply 置为引导转人工的话术，照发
     */
    const SOFT_DEGRADE_REASONS = ['no_sources', 'low_confidence'];

    /**
     * @param int $msgId 用户消息 store_service_log.id（幂等键）
     * @param int $uid 用户 uid（消息接收方）
     * @param int $kefuUid 客服 uid（AI 回复的落库身份）
     * @param string $msn 用户消息原文
     * @param int $formType 客户端来源
     * @param string $traceId PHP 侧生成的链路 id
     * @param int $addTime 用户消息发送时刻（unix），用于计算 SLA 剩余
     * @return bool 永远 true（不重投）
     */
    public function doJob(int $msgId, int $uid, int $kefuUid, string $msn, int $formType, string $traceId, int $addTime): bool
    {
        $startedAt = microtime(true);
        $ctx = ['trace_id' => $traceId, 'msg_id' => $msgId, 'uid' => $uid, 'kefu_uid' => $kefuUid];
        try {
            //开关可能在入队后被关掉，执行时再校验一次
            if (sys_config('ai_service_open', 0) != 1) {
                return true;
            }
            //消息行已被删除（后台清理聊天记录等场景）
            if (!Db::name('store_service_log')->where('id', $msgId)->value('id')) {
                return true;
            }

            //整体 SLA：从消息发送时刻起算剩余时间，不足 2s 直接放弃（超时不重试原则）
            $slaMs = max(3.0, (float)sys_config('ai_service_timeout', 20)) * 1000;
            $deadlineMs = $slaMs - max(0, time() - $addTime) * 1000;
            if ($deadlineMs < 2000) {
                Log::warning('[ai] deadline_blown ' . json_encode($ctx + ['deadline_ms' => $deadlineMs], JSON_UNESCAPED_UNICODE));
                return true;
            }

            $payload = [
                'user_msg_id' => (string)$msgId,
                'uid'         => $uid,
                'kefu_uid'    => $kefuUid,
                'message'     => mb_substr($msn, 0, 2000),
                'history'     => $this->recentHistory($uid, $kefuUid, $msgId),
                'kb_enabled'  => true,
                'deadline_ms' => round($deadlineMs),
            ];

            $base = rtrim((string)sys_config('ai_service_url', 'http://host.docker.internal:8000'), '/');
            $resp = (new AiClient())->postJson($base . '/api/chat', $payload, $traceId, 1, 15, $deadlineMs);
            $ctx['http'] = ['status' => $resp['status'], 'attempts' => $resp['attempts'], 'duration_ms' => $resp['duration_ms']];

            if (!$resp['ok']) {
                $ctx['error'] = $resp['error'];
                Log::warning('[ai] upstream_failed ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
                //传输层失败（连不上/超时/非5xx的4xx等）与 ai-service 应答降级同样对待：
                //默认静默；开启 ai_service_notify_on_failure 后发送配置话术
                if (sys_config('ai_service_notify_on_failure', 0) != 1) {
                    return true;
                }
                $this->saveAndPushReply(
                    (string)sys_config('ai_service_fallback_text', '抱歉，智能客服暂时无法回复，请稍后再试或联系人工客服。'),
                    $uid, $kefuUid, $formType
                );
                $ctx['total_ms'] = round((microtime(true) - $startedAt) * 1000, 1);
                Log::info('[ai] fallback_sent ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
                return true;
            }

            $envelope = json_decode($resp['body'], true);
            if (!is_array($envelope) || (int)($envelope['code'] ?? -1) !== 0 || !is_array($envelope['data'] ?? null)
                || !isset($envelope['data']['reply'])) {
                $ctx['error'] = 'bad_envelope';
                Log::warning('[ai] bad_envelope ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
                return true;
            }
            $data = $envelope['data'];
            $ctx += [
                'degraded'   => !empty($data['degraded']),
                'reason'     => (string)($data['reason'] ?? ''),
                'kb_used'    => !empty($data['kb_used']),
                'ai_latency' => $envelope['latency_ms'] ?? null,
            ];

            $reply = trim((string)$data['reply']);
            if ($reply === '') {
                Log::warning('[ai] empty_reply ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
                return true;
            }

            if (!empty($data['degraded'])) {
                $reason = (string)($data['reason'] ?? '');
                if (in_array($reason, self::HARD_FAIL_REASONS, true)) {
                    if (sys_config('ai_service_notify_on_failure', 0) != 1) {
                        //硬故障静默降级：不发消息，用户侧零感知
                        Log::warning('[ai] silent_degrade ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
                        return true;
                    }
                    $reply = (string)sys_config('ai_service_fallback_text', '抱歉，智能客服暂时无法回复，请稍后再试或联系人工客服。');
                }
                //软降级（no_sources/low_confidence）：reply 本身就是引导转人工话术，照发
            }

            $this->saveAndPushReply($reply, $uid, $kefuUid, $formType);
            $ctx['total_ms'] = round((microtime(true) - $startedAt) * 1000, 1);
            Log::info('[ai] reply_sent ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
            return true;
        } catch (\Throwable $e) {
            $ctx['error'] = $e->getMessage();
            Log::error('[ai] job_error ' . json_encode($ctx, JSON_UNESCAPED_UNICODE));
            return true;
        }
    }

    /**
     * 最近 N 条文本聊天（双向），按时间正序，映射为 ai-service history 契约 {role, text}
     * @return array
     */
    protected function recentHistory(int $uid, int $kefuUid, int $msgId): array
    {
        $limit = (int)sys_config('ai_service_history_limit', 6);
        if ($limit <= 0) {
            return [];
        }
        $rows = Db::name('store_service_log')
            //PDO 预处理不支持同名占位符复用，每个位置独立命名
            ->whereRaw('((uid = :u1 AND to_uid = :k1) OR (uid = :k2 AND to_uid = :u2))', [
                'u1' => $uid, 'k1' => $kefuUid,
                'k2' => $kefuUid, 'u2' => $uid,
            ])
            ->where('msn_type', 1)
            ->where('id', '<', $msgId)
            ->field('uid, msn')
            ->order('id', 'desc')
            ->limit($limit)
            ->select()
            ->toArray();

        $history = [];
        foreach (array_reverse($rows) as $row) {
            $msn = trim((string)$row['msn']);
            if ($msn === '') {
                continue;
            }
            $history[] = [
                'role' => ((int)$row['uid'] === $uid) ? 'user' : 'kefu',
                'text' => mb_substr($msn, 0, 500),
            ];
        }
        return $history;
    }

    /**
     * 以客服身份落库并跨进程推送回复——完全复刻 ChatHandle 关键词自动回复的通路，
     * 用户端聊天页对 AI 回复零感知（同一条 store_service_log + ws reply 事件）
     */
    protected function saveAndPushReply(string $reply, int $uid, int $kefuUid, int $formType): void
    {
        /** @var StoreServiceLogServices $logServices */
        $logServices = app()->make(StoreServiceLogServices::class);
        $autoReply = [
            'to_uid'     => $uid,
            'msn_type'   => 1,
            'msn'        => $reply,
            'uid'        => $kefuUid,
            'add_time'   => time(),
            'is_tourist' => 0,
            'type'       => 1,
        ];
        $autoReply = $logServices->save($autoReply)->toArray();
        $autoReply['_add_time'] = $autoReply['add_time'];
        $autoReply['add_time'] = strtotime($autoReply['add_time']);

        $kefuInfo = app()->make(StoreServiceServices::class)->get(['uid' => $kefuUid], ['nickname', 'avatar']);
        /** @var StoreServiceRecordServices $serviceRecored */
        $serviceRecored = app()->make(StoreServiceRecordServices::class);
        $replyMessagesCount = $logServices->getMessageNum(['uid' => $kefuUid, 'to_uid' => $uid, 'type' => 0, 'is_tourist' => 0]);
        $autoReply['recored'] = $serviceRecored->saveRecord($kefuUid, $uid, $reply, $formType, 1, $replyMessagesCount, false, $kefuInfo['nickname'] ?? '', $kefuInfo['avatar'] ?? '');

        //queue 进程 → channel(40003, trigger=crmeb_chat) → chat 进程(40002) → 用户连接
        ChannelService::instance()->setTrigger('crmeb_chat')->send('reply', $autoReply, [$uid]);
    }
}
