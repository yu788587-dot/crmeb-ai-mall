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

namespace crmeb\services\ai;

/**
 * AI 微服务 HTTP 客户端（窗口3 集成：超时分层 + 受限重试）
 *
 * 与 ai-service(app/providers/siliconflow.py) 的 RetryPolicy 对齐：
 *  - 连接 1s 快速失败（CONNECTTIMEOUT）
 *  - 单次 HTTP 上限默认 15s（TIMEOUT），受整体预算约束
 *  - 仅「连接类失败 / HTTP 5xx」重试一次；超时(28)与 4xx 绝不重试
 */
class AiClient
{
    /**
     * 连接类 curl 错误码（DNS 解析失败 / 无法建立连接）——仅这两类允许重试
     * 28=CURLE_OPERATION_TIMEDOUT 属于超时，按契约不重试
     */
    const RETRYABLE_ERRNO = [6, 7];

    /**
     * POST JSON（当前仅 /api/chat 使用）
     *
     * @param string $url 完整地址，如 http://host.docker.internal:8000/api/chat
     * @param array $payload 请求体（ChatRequest schema）
     * @param string $traceId 贯穿双端日志的链路 id
     * @param int $connectTimeout 连接超时秒数（默认 1，快速失败）
     * @param int $attemptTimeout 单次请求超时秒数（默认 15）
     * @param float $budgetMs 整体时间预算毫秒（SLA 剩余），耗尽即放弃
     * @return array{ok:bool,status:int,body:string,error:string,duration_ms:float,attempts:int}
     */
    public function postJson(string $url, array $payload, string $traceId, int $connectTimeout = 1, int $attemptTimeout = 15, float $budgetMs = 20000.0): array
    {
        $started = microtime(true);
        $attempts = 0;
        $lastError = '';
        $lastStatus = 0;
        $lastBody = '';

        while ($attempts < 2) {
            $remainingMs = $budgetMs - (microtime(true) - $started) * 1000;
            if ($remainingMs < 500) {
                $lastError = 'budget_exhausted' . ($lastError ? ': ' . $lastError : '');
                break;
            }
            $attempts++;
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => json_encode($payload, JSON_UNESCAPED_UNICODE),
                CURLOPT_HTTPHEADER     => [
                    'Content-Type: application/json',
                    'X-Trace-Id: ' . $traceId,
                ],
                CURLOPT_CONNECTTIMEOUT => $connectTimeout,
                CURLOPT_TIMEOUT        => (int)min($attemptTimeout, $remainingMs / 1000),
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_HEADER         => false,
            ]);
            $body = curl_exec($ch);
            $errno = curl_errno($ch);
            $error = curl_error($ch);
            $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            curl_close($ch);

            $lastStatus = $status;
            $lastBody = ($body === false || $body === null) ? '' : (string)$body;
            if ($errno === 0 && $status >= 200 && $status < 300) {
                return [
                    'ok'          => true,
                    'status'      => $status,
                    'body'        => $lastBody,
                    'error'       => '',
                    'duration_ms' => round((microtime(true) - $started) * 1000, 1),
                    'attempts'    => $attempts,
                ];
            }
            $lastError = $errno !== 0 ? sprintf('curl(%d): %s', $errno, $error) : sprintf('http_status:%d', $status);
            $retryable = in_array($errno, self::RETRYABLE_ERRNO, true)
                || ($errno === 0 && $status >= 500);
            if (!$retryable) {
                break;
            }
        }

        return [
            'ok'          => false,
            'status'      => $lastStatus,
            'body'        => $lastBody,
            'error'       => $lastError,
            'duration_ms' => round((microtime(true) - $started) * 1000, 1),
            'attempts'    => $attempts,
        ];
    }
}
