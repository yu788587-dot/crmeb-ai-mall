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

namespace app\adminapi\controller\v1\ai;

use app\adminapi\controller\AuthController;
use crmeb\services\ai\AiClient;

/**
 * 后台 AI 文案生成（窗口5 第二用例）
 *
 * 与客服 chat 共用同一 ai-service / 同一观测链路（trace/熔断/背压），
 * 差异仅在：无 RAG、无幂等、无同会话串行——后台管理员主动触发的短任务。
 * 任何失败（服务不可达/熔断开启/降级）都以 fail 返回给前端提示，绝不阻塞商品编辑本身。
 */
class Copywriting extends AuthController
{
    /**
     * 开放给商品编辑页的文案类型（与 ai-service CopywritingRequest.kind 对齐）
     * 商品名称=product_title / 卖点=product_sell_point / 详情=product_desc / SEO=product_seo_keywords
     */
    const KINDS = ['product_title', 'product_sell_point', 'product_desc', 'product_seo_keywords'];

    /**
     * 生成文案
     * @param string kind 文案类型（见 KINDS）
     * @param string seed 生成素材（商品名/类目/已有卖点等）
     * @param string tone 语气 professional|friendly|promotional
     * @param int max_tokens 生成上限（50-2000）
     */
    public function generate()
    {
        $kind = trim((string)$this->request->param('kind', ''));
        if (!in_array($kind, self::KINDS, true)) {
            return app('json')->fail('不支持的文案类型');
        }
        $seed = trim((string)$this->request->param('seed', ''));
        if ($seed === '') {
            return app('json')->fail('请先填写文案素材（商品名/关键词等）');
        }
        if (mb_strlen($seed) > 2000) {
            $seed = mb_substr($seed, 0, 2000);
        }
        $tone = trim((string)$this->request->param('tone', 'professional'));
        if (!in_array($tone, ['professional', 'friendly', 'promotional'], true)) {
            $tone = 'professional';
        }
        $maxTokens = min(2000, max(50, (int)$this->request->param('max_tokens/d', 400)));

        $base = rtrim((string)sys_config('ai_service_url', 'http://host.docker.internal:8000'), '/');
        $traceId = 'ai-' . bin2hex(random_bytes(8));
        $payload = ['kind' => $kind, 'seed' => $seed, 'tone' => $tone, 'max_tokens' => $maxTokens];
        // 无聊天 SLA 约束：等满 ai-service 的 LLM 15s 上限 + SLA 20s 预算（21s 必能收到应答或降级信封）
        $resp = (new AiClient())->postJson($base . '/api/copywriting', $payload, $traceId, 1, 21, 25000.0);

        if (!$resp['ok']) {
            return app('json')->fail('AI 服务暂时不可用：' . $resp['error']);
        }
        $envelope = json_decode($resp['body'], true);
        if (!is_array($envelope)) {
            return app('json')->fail('AI 服务响应解析失败');
        }
        if ((int)($envelope['code'] ?? -1) !== 0) {
            return app('json')->fail('AI 生成暂时不可用（' . (string)($envelope['error'] ?? 'unknown') . '），请稍后重试');
        }
        $data = is_array($envelope['data'] ?? null) ? $envelope['data'] : [];
        if (!empty($data['degraded'])) {
            return app('json')->fail('AI 生成暂时不可用（' . (string)($data['reason'] ?? 'degraded') . '），请稍后重试');
        }
        $text = trim((string)($data['text'] ?? ''));
        if ($text === '') {
            return app('json')->fail('AI 未返回有效文案，请稍后重试');
        }

        return app('json')->success([
            'text'         => $text,
            'kind'         => $kind,
            'model'        => (string)($data['model'] ?? ''),
            'tokens'       => (int)($data['tokens'] ?? 0),
            'trace_id'     => (string)($envelope['trace_id'] ?? $traceId),
            'ai_latency_ms' => (float)($envelope['latency_ms'] ?? 0),
            'duration_ms'  => $resp['duration_ms'],
        ]);
    }
}
