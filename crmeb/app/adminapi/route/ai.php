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
use think\facade\Route;

/**
 * AI 运行监控（窗口4 可观测轨道一）：eb_ai_call_log 埋点表只读聚合与明细
 */
Route::group('ai', function () {

    Route::group(function () {
        //汇总概览（KPI/分布/分位数/耗时分解）
        Route::get('monitor/summary', 'v1.ai.Monitor/summary')->option(['real_name' => 'AI监控汇总']);
        //小时趋势
        Route::get('monitor/trend', 'v1.ai.Monitor/trend')->option(['real_name' => 'AI监控趋势']);
        //明细列表
        Route::get('monitor/list', 'v1.ai.Monitor/list')->option(['real_name' => 'AI监控明细']);
        //trace 链路查询
        Route::get('monitor/trace', 'v1.ai.Monitor/trace')->option(['real_name' => 'AI监控trace查询']);
        //ai-service 健康透传
        Route::get('monitor/health', 'v1.ai.Monitor/health')->option(['real_name' => 'AI监控健康探针']);
    })->option(['parent' => 'ai', 'cate_name' => 'AI 运行监控']);

})->middleware([
    \app\http\middleware\AllowOriginMiddleware::class,
    \app\adminapi\middleware\AdminAuthTokenMiddleware::class,
    \app\adminapi\middleware\AdminCheckRoleMiddleware::class,
    \app\adminapi\middleware\AdminLogMiddleware::class
])->option(['mark' => 'ai', 'mark_name' => 'AI 监控']);
