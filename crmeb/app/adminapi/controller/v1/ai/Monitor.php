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
use think\facade\App;
use think\facade\Db;

/**
 * AI 运行监控（窗口4 可观测轨道一）
 *
 * 数据源唯一：eb_ai_call_log（PHP 侧埋点表）。指标语义：
 *  - outcome: success 成功应答（含软降级，degraded=1 区分）/ silent 硬故障静默 /
 *             notify 故障发送降级话术 / rejected 派发被背压拒绝 / error 任务异常
 *  - 延迟分位数取 success 行的 total_ms（回答「用户等多久」），耗时分解用 http_ms/ai_ms
 */
class Monitor extends AuthController
{
    /** 明细表字段（list/trace 返回） */
    const LIST_FIELDS = 'id,trace_id,event,outcome,uid,kefu_uid,msg_id,http_status,attempts,http_ms,ai_ms,total_ms,kb_used,degraded,reason,prompt_tokens,completion_tokens,error,add_time';

    /**
     * 汇总概览（KPI 卡片 + 结果分布 + 降级原因 + 耗时分解）
     * @param int hours 统计窗口小时数（默认 24）
     */
    public function summary()
    {
        [$from] = $this->window();
        $base = Db::name('ai_call_log')->where('add_time', '>=', $from);
        $total = (clone $base)->count();

        $byOutcome = $total > 0
            ? $base->field('outcome, COUNT(*) AS c')->group('outcome')->select()->toArray()
            : [];
        $outcomes = ['success' => 0, 'silent' => 0, 'notify' => 0, 'rejected' => 0, 'error' => 0];
        foreach ($byOutcome as $row) {
            $outcomes[$row['outcome']] = (int)$row['c'];
        }

        $byEvent = $total > 0
            ? Db::name('ai_call_log')->where('add_time', '>=', $from)
                ->field('event, outcome, COUNT(*) AS c')->group('event, outcome')->select()->toArray()
            : [];

        $byReason = $total > 0
            ? Db::name('ai_call_log')->where('add_time', '>=', $from)->where('reason', '<>', '')
                ->field('reason, outcome, COUNT(*) AS c')->group('reason, outcome')->select()->toArray()
            : [];

        //延迟与耗时分解：只统计成功应答的行（用户真实等到的回复）
        $successQ = Db::name('ai_call_log')->where('add_time', '>=', $from)->where('outcome', 'success');
        $successCount = (clone $successQ)->count();
        $totals = $successCount > 0
            ? $successQ->column('total_ms')
            : [];
        $httpMs = $successCount > 0
            ? Db::name('ai_call_log')->where('add_time', '>=', $from)->where('outcome', 'success')->column('http_ms')
            : [];
        $aiMs = $successCount > 0
            ? Db::name('ai_call_log')->where('add_time', '>=', $from)->where('outcome', 'success')->column('ai_ms')
            : [];
        $agg = $successCount > 0
            ? Db::name('ai_call_log')->where('add_time', '>=', $from)->where('outcome', 'success')
                ->field('COALESCE(AVG(prompt_tokens),0) AS avg_prompt, COALESCE(AVG(completion_tokens),0) AS avg_completion,
                         COALESCE(SUM(prompt_tokens),0) AS sum_prompt, COALESCE(SUM(completion_tokens),0) AS sum_completion,
                         SUM(kb_used) AS kb_used, SUM(degraded) AS degraded, SUM(attempts > 1) AS retried')
                ->find() : [];

        return app('json')->success([
            'window_hours' => (int)($this->request->param('hours/d', 24)),
            'total'        => $total,
            'outcomes'     => $outcomes,
            'success_rate' => $total > 0 ? round($outcomes['success'] / $total * 100, 2) : null,
            'latency'      => [
                'total' => $this->percentiles($totals),
                'http'  => $this->percentiles($httpMs),
                'ai'    => $this->percentiles($aiMs),
            ],
            'tokens'       => [
                'avg_prompt'     => isset($agg['avg_prompt']) ? round((float)$agg['avg_prompt'], 1) : null,
                'avg_completion' => isset($agg['avg_completion']) ? round((float)$agg['avg_completion'], 1) : null,
                'sum_prompt'     => (int)($agg['sum_prompt'] ?? 0),
                'sum_completion' => (int)($agg['sum_completion'] ?? 0),
            ],
            'kb_used'      => (int)($agg['kb_used'] ?? 0),
            'kb_hit_rate'  => $successCount > 0 ? round((int)($agg['kb_used'] ?? 0) / $successCount * 100, 2) : null,
            'degraded'     => (int)($agg['degraded'] ?? 0),
            'retried'      => (int)($agg['retried'] ?? 0),
            'success_count'=> $successCount,
            'by_event'     => $byEvent,
            'by_reason'    => $byReason,
        ]);
    }

    /**
     * 趋势（按小时分桶：总量/成功/静默/降级话术/拒绝/平均耗时）
     * @param int hours 默认 24
     */
    public function trend()
    {
        [$from, $hours] = $this->window();
        $rows = Db::name('ai_call_log')->where('add_time', '>=', $from)
            ->field("FROM_UNIXTIME(add_time, '%Y-%m-%d %H:00') AS bucket,
                     COUNT(*) AS total,
                     SUM(outcome='success') AS success,
                     SUM(outcome='silent') AS silent,
                     SUM(outcome='notify') AS notify,
                     SUM(outcome='rejected') AS rejected,
                     SUM(outcome='error') AS error,
                     SUM(degraded=1) AS degraded,
                     COALESCE(AVG(CASE WHEN outcome='success' THEN total_ms END),0) AS avg_ms")
            ->group('bucket')->select()->toArray();
        $indexed = [];
        foreach ($rows as $row) {
            $indexed[$row['bucket']] = $row;
        }
        $buckets = [];
        $n = max(1, $hours);
        for ($i = $n - 1; $i >= 0; $i--) {
            $key = date('Y-m-d H:00', strtotime("-{$i} hour"));
            $row = $indexed[$key] ?? null;
            $buckets[] = [
                'bucket'   => $key,
                'total'    => (int)($row['total'] ?? 0),
                'success'  => (int)($row['success'] ?? 0),
                'silent'   => (int)($row['silent'] ?? 0),
                'notify'   => (int)($row['notify'] ?? 0),
                'rejected' => (int)($row['rejected'] ?? 0),
                'error'    => (int)($row['error'] ?? 0),
                'degraded' => (int)($row['degraded'] ?? 0),
                'avg_ms'   => $row ? round((float)$row['avg_ms'], 1) : null,
            ];
        }
        return app('json')->success($buckets);
    }

    /**
     * 明细列表（筛选：event/outcome/trace_id/uid/日期范围，分页）
     */
    public function list()
    {
        [$where, $page, $limit] = $this->listWhere();
        $query = Db::name('ai_call_log')->where($where);
        $total = (clone $query)->count();
        $rows = $query->field(self::LIST_FIELDS)
            ->order('id', 'desc')
            ->page((int)$page, (int)$limit)
            ->select()->toArray();
        return app('json')->success(['count' => $total, 'list' => $rows]);
    }

    /**
     * trace 查询：一次问答的完整链路（埋点行 + 用户消息原文 + AI 回复原文）
     * @param string $trace_id
     */
    public function trace()
    {
        $traceId = trim((string)$this->request->param('trace_id', ''));
        if ($traceId === '') {
            return app('json')->fail('缺少 trace_id');
        }
        $rows = Db::name('ai_call_log')->where('trace_id', $traceId)
            ->field(self::LIST_FIELDS)->order('id', 'asc')->select()->toArray();
        if (!$rows) {
            return app('json')->fail('未找到该 trace_id 的记录');
        }
        $conversation = [];
        $msgId = (int)($rows[0]['msg_id'] ?? 0);
        if ($msgId > 0) {
            $userMsg = Db::name('store_service_log')->where('id', $msgId)->field('id,uid,to_uid,msn,add_time')->find();
            if ($userMsg) {
                $conversation[] = ['role' => 'user', 'msn' => $userMsg['msn'], 'add_time' => $userMsg['add_time']];
            }
            //AI/降级回复：消息之后该客服发给该用户的第一行
            $kefuUid = (int)($rows[0]['kefu_uid'] ?? 0);
            $uid = (int)($rows[0]['uid'] ?? 0);
            if ($kefuUid > 0) {
                $reply = Db::name('store_service_log')
                    ->where('uid', $kefuUid)->where('to_uid', $uid)
                    ->where('id', '>', $msgId)->where('msn_type', 1)
                    ->order('id', 'asc')->find();
                if ($reply) {
                    $conversation[] = ['role' => 'ai', 'msn' => $reply['msn'], 'add_time' => $reply['add_time']];
                }
            }
        }
        return app('json')->success(['trace_id' => $traceId, 'calls' => $rows, 'conversation' => $conversation]);
    }

    /**
     * ai-service 健康探针（透传 /api/health：breaker/kb/backpressure 实时状态）
     */
    public function health()
    {
        $base = rtrim((string)sys_config('ai_service_url', 'http://host.docker.internal:8000'), '/');
        $resp = (new AiClient())->getJson($base . '/api/health', 'health-' . substr(md5((string)time()), 0, 8));
        $data = json_decode($resp['body'], true);
        if (!$resp['ok'] || !is_array($data)) {
            return app('json')->success(['reachable' => false, 'error' => $resp['error']]);
        }
        $data['reachable'] = true;
        return app('json')->success($data);
    }

    /**
     * 统计窗口参数：[起始 unix 秒, 小时数]
     */
    protected function window(): array
    {
        $hours = min(720, max(1, (int)$this->request->param('hours/d', 24)));
        return [time() - $hours * 3600, $hours];
    }

    /**
     * 明细筛选条件 + 分页参数
     */
    protected function listWhere(): array
    {
        $where = [];
        $event = trim((string)$this->request->param('event', ''));
        if ($event !== '') {
            $where[] = ['event', '=', $event];
        }
        $outcome = trim((string)$this->request->param('outcome', ''));
        if ($outcome !== '') {
            $where[] = ['outcome', '=', $outcome];
        }
        $traceId = trim((string)$this->request->param('trace_id', ''));
        if ($traceId !== '') {
            $where[] = ['trace_id', 'like', '%' . $traceId . '%'];
        }
        $uid = (int)$this->request->param('uid/d', 0);
        if ($uid > 0) {
            $where[] = ['uid', '=', $uid];
        }
        $date = (string)$this->request->param('date', '');
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
            $where[] = ['add_time', '>=', strtotime($date)];
            $where[] = ['add_time', '<', strtotime($date) + 86400];
        }
        $page = max(1, (int)$this->request->param('page/d', 1));
        $limit = min(100, max(10, (int)$this->request->param('limit/d', 20)));
        return [$where, $page, $limit];
    }

    /**
     * 分位数（P50/P90/P95/P99）+ 均值/最大值；输入为秒保留 1 位的毫秒数组
     */
    protected function percentiles(array $values): array
    {
        if (!$values) {
            return ['p50' => null, 'p90' => null, 'p95' => null, 'p99' => null, 'avg' => null, 'max' => null];
        }
        sort($values);
        $n = count($values);
        $pick = static function (float $p) use ($values, $n): float {
            $idx = (int)max(0, min($n - 1, (int)ceil($p / 100 * $n) - 1));
            return round((float)$values[$idx], 1);
        };
        return [
            'p50' => $pick(50),
            'p90' => $pick(90),
            'p95' => $pick(95),
            'p99' => $pick(99),
            'avg' => round(array_sum($values) / $n, 1),
            'max' => round((float)end($values), 1),
        ];
    }
}
