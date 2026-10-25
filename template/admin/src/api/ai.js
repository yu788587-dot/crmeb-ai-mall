// +----------------------------------------------------------------------
// | CRMEB [ CRMEB赋能开发者，助力企业发展 ]
// +----------------------------------------------------------------------
// | Copyright (c) 2016~2026 https://www.crmeb.com All rights reserved.
// +----------------------------------------------------------------------
// | Licensed CRMEB并不是自由软件，未经许可不能去掉CRMEB相关版权
// +----------------------------------------------------------------------
// | Author: CRMEB Team <admin@crmeb.com>
// +----------------------------------------------------------------------

import request from '@/libs/request';

/**
 * AI 运行监控（crmeb-ai-mall 窗口4）
 * 数据源：eb_ai_call_log（PHP 侧埋点表）+ ai-service /api/health 透传
 */

// 汇总概览（KPI/分布/分位数/耗时分解），params: { hours }
export function aiSummary(params) {
  return request({
    url: '/ai/monitor/summary',
    method: 'get',
    params,
  });
}

// 小时趋势，params: { hours }
export function aiTrend(params) {
  return request({
    url: '/ai/monitor/trend',
    method: 'get',
    params,
  });
}

// 明细列表，params: { page, limit, event, outcome, trace_id, uid, date }
export function aiMonitorList(params) {
  return request({
    url: '/ai/monitor/list',
    method: 'get',
    params,
  });
}

// trace 链路查询，params: { trace_id }
export function aiTrace(params) {
  return request({
    url: '/ai/monitor/trace',
    method: 'get',
    params,
  });
}

// ai-service 健康探针（breaker/kb/backpressure 实时状态）
export function aiHealth() {
  return request({
    url: '/ai/monitor/health',
    method: 'get',
  });
}
