<template>
  <div class="ai-monitor" v-loading="loading">
    <!-- 头部：统计窗口 + 健康状态 -->
    <el-card :bordered="false" shadow="never" class="ivu-mb">
      <div class="monitor-head">
        <div class="monitor-title">
          <i class="el-icon-data-analysis" style="color: var(--prev-color-primary); margin-right: 6px"></i>
          <span>AI 智能客服运行监控</span>
          <el-tag v-if="health.reachable" size="small" :type="breakerTag" style="margin-left: 10px">
            ai-service {{ health.status }} · 熔断 {{ health.breaker_state }}
          </el-tag>
          <el-tag v-else-if="health.loaded" size="small" type="danger" style="margin-left: 10px">ai-service 不可达</el-tag>
        </div>
        <div>
          <el-select v-model="hours" size="small" style="width: 130px" @change="loadAll">
            <el-option :value="6" label="最近 6 小时"></el-option>
            <el-option :value="24" label="最近 24 小时"></el-option>
            <el-option :value="72" label="最近 3 天"></el-option>
            <el-option :value="168" label="最近 7 天"></el-option>
          </el-select>
          <el-button size="small" icon="el-icon-refresh" style="margin-left: 8px" @click="loadAll">刷新</el-button>
        </div>
      </div>
      <div v-if="health.reachable" class="monitor-health">
        <span>KB 文档 {{ health.kb_documents }} 篇</span>
        <el-divider direction="vertical"></el-divider>
        <span>并发 {{ health.backpressure_inflight_text }}</span>
        <el-divider direction="vertical"></el-divider>
        <span>连续失败 {{ health.breaker_failures }} 次 / 熔断触发 {{ health.breaker_trips }} 次</span>
        <el-divider direction="vertical"></el-divider>
        <span>{{ health.provider_text }}</span>
      </div>
    </el-card>

    <!-- KPI 卡片 -->
    <el-row :gutter="16" class="ivu-mb">
      <el-col :xl="6" :lg="6" :md="12" :sm="12" :xs="12" v-for="card in kpiCards" :key="card.label">
        <el-card :bordered="false" shadow="never" class="kpi-card">
          <div class="kpi-label">{{ card.label }}</div>
          <div class="kpi-value" :style="{ color: card.color || '' }">{{ card.value }}</div>
          <div class="kpi-sub">{{ card.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 趋势 + 结果分布 -->
    <el-row :gutter="16" class="ivu-mb">
      <el-col :xl="16" :lg="16" :md="24" :sm="24" :xs="24">
        <el-card :bordered="false" shadow="never">
          <div class="card-title">调用量与平均耗时趋势（按小时）</div>
          <div ref="trendChart" style="height: 320px"></div>
        </el-card>
      </el-col>
      <el-col :xl="8" :lg="8" :md="24" :sm="24" :xs="24">
        <el-card :bordered="false" shadow="never">
          <div class="card-title">处理结果分布</div>
          <div ref="outcomeChart" style="height: 320px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 延迟分解 + 降级原因 -->
    <el-row :gutter="16" class="ivu-mb">
      <el-col :xl="10" :lg="10" :md="24" :sm="24" :xs="24">
        <el-card :bordered="false" shadow="never">
          <div class="card-title">延迟分解（成功应答，毫秒）</div>
          <el-table :data="latencyRows" size="small" :header-cell-style="{ 'font-weight': 'bold' }">
            <el-table-column prop="name" label="阶段" width="140"></el-table-column>
            <el-table-column prop="p50" label="P50"></el-table-column>
            <el-table-column prop="p90" label="P90"></el-table-column>
            <el-table-column prop="p95" label="P95"></el-table-column>
            <el-table-column prop="p99" label="P99"></el-table-column>
            <el-table-column prop="avg" label="均值"></el-table-column>
            <el-table-column prop="max" label="最大"></el-table-column>
          </el-table>
          <div class="monitor-tip">
            total = 任务出队到推送全程；http = PHP→ai-service 往返；ai = ai-service 内部（RAG+LLM）。
            token 消耗：输入 {{ fmtNum(tokens.sum_prompt) }} / 输出 {{ fmtNum(tokens.sum_completion) }}（平均
            {{ tokens.avg_prompt }} + {{ tokens.avg_completion }}）
          </div>
        </el-card>
      </el-col>
      <el-col :xl="14" :lg="14" :md="24" :sm="24" :xs="24">
        <el-card :bordered="false" shadow="never">
          <div class="card-title">降级 / 故障原因分布</div>
          <div ref="reasonChart" style="height: 260px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 明细 -->
    <el-card :bordered="false" shadow="never">
      <div class="monitor-head" style="margin-bottom: 12px">
        <div class="card-title" style="margin: 0">调用明细</div>
        <div>
          <el-select v-model="table.outcome" size="small" clearable placeholder="结果" style="width: 110px">
            <el-option v-for="(v, k) in outcomeMap" :key="k" :value="k" :label="v.text"></el-option>
          </el-select>
          <el-input
            v-model="table.traceId"
            size="small"
            clearable
            placeholder="trace_id"
            style="width: 200px; margin: 0 8px"
            @keyup.enter.native="loadList(1)"
          ></el-input>
          <el-button size="small" type="primary" @click="loadList(1)">查询</el-button>
        </div>
      </div>
      <el-table :data="table.list" size="small" stripe>
        <el-table-column label="时间" width="150">
          <template slot-scope="scope">{{ fmtTime(scope.row.add_time) }}</template>
        </el-table-column>
        <el-table-column label="trace_id" width="180">
          <template slot-scope="scope">
            <el-link type="primary" :underline="false" @click="showTrace(scope.row.trace_id)">
              {{ scope.row.trace_id }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column label="事件" width="170">
          <template slot-scope="scope">{{ scope.row.event }}</template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template slot-scope="scope">
            <el-tag size="mini" :type="outcomeMap[scope.row.outcome] ? outcomeMap[scope.row.outcome].tag : 'info'">
              {{ scope.row.outcome }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总耗时" width="90" align="right">
          <template slot-scope="scope">{{ scope.row.total_ms ? scope.row.total_ms + 'ms' : '-' }}</template>
        </el-table-column>
        <el-table-column label="HTTP" width="90" align="right">
          <template slot-scope="scope">{{ scope.row.http_ms ? scope.row.http_ms + 'ms' : '-' }}</template>
        </el-table-column>
        <el-table-column label="尝试" width="60" align="right">
          <template slot-scope="scope">{{ scope.row.attempts || '-' }}</template>
        </el-table-column>
        <el-table-column label="KB" width="60" align="center">
          <template slot-scope="scope">
            <i v-if="scope.row.kb_used" class="el-icon-check" style="color: #00cc66"></i>
            <i v-else class="el-icon-minus"></i>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" width="140"></el-table-column>
        <el-table-column label="Token(入/出)" width="110" align="right">
          <template slot-scope="scope">{{ scope.row.prompt_tokens }} / {{ scope.row.completion_tokens }}</template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip></el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 14px; text-align: right"
        background
        layout="total, prev, pager, next, sizes"
        :total="table.count"
        :current-page="table.page"
        :page-size="table.limit"
        :page-sizes="[15, 30, 50]"
        @current-change="loadList"
        @size-change="(s) => loadList(1, s)"
      ></el-pagination>
    </el-card>

    <!-- trace 弹窗 -->
    <el-dialog title="调用链路详情" :visible.sync="trace.visible" width="720px" append-to-body>
      <div class="trace-id">trace_id：{{ trace.id }}</div>
      <div class="trace-msg" v-for="(msg, i) in trace.conversation" :key="i" :class="msg.role">
        <div class="trace-msg-role">{{ msg.role === 'user' ? '用户' : 'AI / 降级话术' }}</div>
        <div class="trace-msg-body">{{ msg.msn }}</div>
      </div>
      <el-table :data="trace.calls" size="mini" style="margin-top: 14px">
        <el-table-column prop="event" label="事件" width="140"></el-table-column>
        <el-table-column prop="outcome" label="结果" width="90"></el-table-column>
        <el-table-column prop="http_status" label="HTTP" width="70"></el-table-column>
        <el-table-column prop="attempts" label="尝试" width="60"></el-table-column>
        <el-table-column prop="total_ms" label="总耗时ms" width="90"></el-table-column>
        <el-table-column prop="reason" label="原因" width="120"></el-table-column>
        <el-table-column prop="error" label="错误" show-overflow-tooltip></el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script>
import echarts from 'echarts';
import { aiSummary, aiTrend, aiMonitorList, aiTrace, aiHealth } from '@/api/ai';

const OUTCOME_MAP = {
  success: { text: '成功应答', tag: 'success', color: '#00CC66' },
  silent: { text: '静默放弃', tag: 'info', color: '#909399' },
  notify: { text: '降级话术', tag: 'warning', color: '#E6A23C' },
  rejected: { text: '背压拒绝', tag: 'danger', color: '#F56C6C' },
  error: { text: '任务异常', tag: 'danger', color: '#c0392b' },
};

export default {
  name: 'aiMonitor',
  data() {
    return {
      loading: false,
      hours: 24,
      summary: {},
      trend: [],
      health: { loaded: false, reachable: false },
      table: { list: [], count: 0, page: 1, limit: 15, outcome: '', traceId: '' },
      trace: { visible: false, id: '', conversation: [], calls: [] },
      charts: {},
    };
  },
  computed: {
    outcomeMap() {
      return OUTCOME_MAP;
    },
    tokens() {
      return (this.summary && this.summary.tokens) || {};
    },
    kpiCards() {
      const s = this.summary || {};
      const o = s.outcomes || {};
      const p95 = (s.latency && s.latency.total && s.latency.total.p95) || null;
      const avg = (s.latency && s.latency.total && s.latency.total.avg) || null;
      return [
        { label: '总调用量', value: this.fmtNum(s.total || 0), sub: `成功 ${s.success_count || 0} 次`, color: '' },
        { label: '成功率', value: s.success_rate === null || s.success_rate === undefined ? '-' : s.success_rate + '%', sub: '成功应答 / 总调用', color: '#00CC66' },
        { label: 'P95 总延迟', value: p95 ? p95 + 'ms' : '-', sub: `平均 ${avg ? avg + 'ms' : '-'}`, color: '' },
        { label: 'KB 命中率', value: s.kb_hit_rate === null || s.kb_hit_rate === undefined ? '-' : s.kb_hit_rate + '%', sub: 'RAG 检索到资料的应答', color: '' },
        { label: '静默放弃', value: o.silent || 0, sub: '故障静默、等待人工', color: '#909399' },
        { label: '降级话术', value: o.notify || 0, sub: '软降级 ' + (s.degraded || 0) + ' + 故障通知', color: '#E6A23C' },
        { label: '背压拒绝', value: o.rejected || 0, sub: '队列超限未入队', color: '#F56C6C' },
        { label: '重试次数', value: s.retried || 0, sub: 'attempts > 1 的调用', color: '' },
      ];
    },
    latencyRows() {
      const l = (this.summary && this.summary.latency) || {};
      const mk = (name, obj) => ({
        name,
        p50: obj && obj.p50 !== null ? obj.p50 : '-',
        p90: obj && obj.p90 !== null ? obj.p90 : '-',
        p95: obj && obj.p95 !== null ? obj.p95 : '-',
        p99: obj && obj.p99 !== null ? obj.p99 : '-',
        avg: obj && obj.avg !== null ? obj.avg : '-',
        max: obj && obj.max !== null ? obj.max : '-',
      });
      return [mk('总耗时 total', l.total), mk('HTTP 往返', l.http), mk('ai-service 内部', l.ai)];
    },
    breakerTag() {
      const st = this.health.breaker_state;
      if (st === 'open') return 'danger';
      if (st === 'half_open') return 'warning';
      return 'success';
    },
  },
  mounted() {
    this.loadAll();
    this.loadHealth();
    this.healthTimer = setInterval(this.loadHealth, 30000);
    window.addEventListener('resize', this.resizeCharts);
  },
  beforeDestroy() {
    clearInterval(this.healthTimer);
    window.removeEventListener('resize', this.resizeCharts);
    Object.keys(this.charts).forEach((k) => {
      if (this.charts[k]) this.charts[k].dispose();
    });
  },
  methods: {
    fmtNum(n) {
      return n === null || n === undefined ? '-' : Number(n).toLocaleString();
    },
    fmtTime(ts) {
      if (!ts) return '-';
      const d = new Date(Number(ts) * 1000);
      const p = (v) => (v < 10 ? '0' + v : v);
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    },
    loadAll() {
      this.loading = true;
      Promise.all([aiSummary({ hours: this.hours }), aiTrend({ hours: this.hours })])
        .then(([summaryRes, trendRes]) => {
          this.summary = summaryRes.data;
          this.trend = trendRes.data || [];
          this.$nextTick(() => {
            this.renderTrend();
            this.renderOutcome();
            this.renderReason();
          });
        })
        .finally(() => {
          this.loading = false;
        });
      this.loadList();
    },
    loadHealth() {
      aiHealth()
        .then((res) => {
          const d = res.data || {};
          this.health = {
            loaded: true,
            reachable: !!d.reachable,
            status: d.status || '-',
            breaker_state: (d.breaker && d.breaker.state) || '-',
            breaker_failures: (d.breaker && d.breaker.consecutive_failures) || 0,
            breaker_trips: (d.breaker && d.breaker.trips) || 0,
            kb_documents: (d.kb && d.kb.documents) || 0,
            backpressure_inflight_text:
              d.backpressure && d.backpressure.max_inflight
                ? `${d.backpressure.max_inflight - d.backpressure.available_permits}/${d.backpressure.max_inflight} + 队列 ${d.backpressure.waiting}/${d.backpressure.max_queue}`
                : '-',
            provider_text:
              d.config && d.config.provider
                ? `${d.config.provider} · ${(d.config.chat_model || '').split('/').pop()}`
                : '-',
          };
        })
        .catch(() => {
          this.health = { loaded: true, reachable: false };
        });
    },
    loadList(page, size) {
      if (page) this.table.page = page;
      if (size) this.table.limit = size;
      aiMonitorList({
        page: this.table.page,
        limit: this.table.limit,
        outcome: this.table.outcome || '',
        trace_id: this.table.traceId || '',
      })
        .then((res) => {
          this.table.list = res.data.list || [];
          this.table.count = res.data.count || 0;
        })
        .catch(() => {});
    },
    showTrace(traceId) {
      aiTrace({ trace_id: traceId })
        .then((res) => {
          this.trace = {
            visible: true,
            id: traceId,
            conversation: res.data.conversation || [],
            calls: res.data.calls || [],
          };
        })
        .catch(() => {});
    },
    resizeCharts() {
      Object.keys(this.charts).forEach((k) => {
        if (this.charts[k]) this.charts[k].resize();
      });
    },
    chart(key) {
      if (!this.charts[key]) {
        this.charts[key] = echarts.init(this.$refs[key]);
      }
      return this.charts[key];
    },
    renderTrend() {
      const data = this.trend || [];
      this.chart('trendChart').setOption(
        {
          tooltip: { trigger: 'axis' },
          legend: { data: ['成功', '静默', '降级话术', '背压拒绝', '异常', '平均耗时'] },
          grid: { left: 16, right: 20, bottom: 10, top: 40, containLabel: true },
          xAxis: { type: 'category', data: data.map((d) => d.bucket.slice(5)) },
          yAxis: [
            { type: 'value', name: '次数', minInterval: 1 },
            { type: 'value', name: 'ms', splitLine: { show: false } },
          ],
          series: [
            { name: '成功', type: 'bar', stack: 'total', data: data.map((d) => d.success), itemStyle: { color: '#00CC66' } },
            { name: '静默', type: 'bar', stack: 'total', data: data.map((d) => d.silent), itemStyle: { color: '#909399' } },
            { name: '降级话术', type: 'bar', stack: 'total', data: data.map((d) => d.notify), itemStyle: { color: '#E6A23C' } },
            { name: '背压拒绝', type: 'bar', stack: 'total', data: data.map((d) => d.rejected), itemStyle: { color: '#F56C6C' } },
            { name: '异常', type: 'bar', stack: 'total', data: data.map((d) => d.error), itemStyle: { color: '#c0392b' } },
            {
              name: '平均耗时',
              type: 'line',
              yAxisIndex: 1,
              data: data.map((d) => d.avg_ms),
              symbol: 'circle',
              symbolSize: 5,
              itemStyle: { color: '#1495EB' },
            },
          ],
        },
        true
      );
    },
    renderOutcome() {
      const o = (this.summary && this.summary.outcomes) || {};
      const data = Object.keys(OUTCOME_MAP).map((k) => ({
        name: OUTCOME_MAP[k].text,
        value: o[k] || 0,
        itemStyle: { color: OUTCOME_MAP[k].color },
      }));
      this.chart('outcomeChart').setOption(
        {
          tooltip: { trigger: 'item', formatter: '{b} : {c} ({d}%)' },
          legend: { bottom: 0, icon: 'circle' },
          series: [
            {
              name: '结果分布',
              type: 'pie',
              radius: ['45%', '68%'],
              center: ['50%', '45%'],
              label: { show: true, formatter: '{b}\n{c}' },
              data,
            },
          ],
        },
        true
      );
    },
    renderReason() {
      const rows = (this.summary && this.summary.by_reason) || [];
      const order = [...rows].sort((a, b) => b.c - a.c);
      this.chart('reasonChart').setOption(
        {
          tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
          grid: { left: 16, right: 30, bottom: 10, top: 16, containLabel: true },
          xAxis: { type: 'value', minInterval: 1 },
          yAxis: { type: 'category', data: order.map((r) => r.reason || '(空)') },
          series: [
            {
              name: '次数',
              type: 'bar',
              barMaxWidth: 18,
              data: order.map((r) => ({ value: r.c, outcome: r.outcome })),
              itemStyle: { color: (p) => (OUTCOME_MAP[p.data.outcome] ? OUTCOME_MAP[p.data.outcome].color : '#1495EB') },
              label: { show: true, position: 'right' },
            },
          ],
        },
        true
      );
    },
  },
};
</script>

<style lang="scss" scoped>
.ai-monitor {
  padding: 0 12px;
}
.monitor-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.monitor-title {
  font-size: 15px;
  font-weight: bold;
  display: flex;
  align-items: center;
}
.monitor-health {
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}
.card-title {
  font-weight: bold;
  margin-bottom: 8px;
}
.kpi-card {
  text-align: center;
  .kpi-label {
    font-size: 12px;
    color: #909399;
  }
  .kpi-value {
    font-size: 26px;
    font-weight: bold;
    line-height: 40px;
  }
  .kpi-sub {
    font-size: 11px;
    color: #c0c4cc;
  }
}
.monitor-tip {
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
  line-height: 1.7;
}
.trace-id {
  font-size: 12px;
  color: #909399;
  margin-bottom: 10px;
}
.trace-msg {
  max-width: 80%;
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  &.user {
    background: #ecf5ff;
  }
  &.ai {
    background: #f0f9eb;
    margin-left: auto;
  }
  .trace-msg-role {
    font-size: 11px;
    color: #909399;
    margin-bottom: 2px;
  }
}
</style>
