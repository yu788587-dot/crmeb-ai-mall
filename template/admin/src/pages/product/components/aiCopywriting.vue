<template>
  <!-- AI 文案生成（crmeb-ai-mall 窗口5）：生成 → 预览可编辑 → 应用
       Copyright (c) 2016~2026 https://www.crmeb.com All rights reserved.
       Licensed under the Apache License 2.0 （基于 CRMEB-KY v6.0.0 二次开发，修改以 git 历史体现） -->
  <el-dialog
    :title="`AI 生成${target.label || '文案'}`"
    :visible.sync="visible"
    width="600px"
    append-to-body
    :close-on-click-modal="false"
  >
    <el-form label-width="76px" size="small" @submit.native.prevent>
      <el-form-item label="语气：">
        <el-radio-group v-model="tone">
          <el-radio label="professional">专业</el-radio>
          <el-radio label="friendly">亲切</el-radio>
          <el-radio label="promotional">促销</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="素材：">
        <el-input
          v-model="seed"
          type="textarea"
          :rows="3"
          maxlength="2000"
          show-word-limit
          placeholder="商品名 / 类目 / 核心卖点等，AI 依此生成；留空则用当前表单内容"
        />
      </el-form-item>
      <el-form-item label="生成结果：">
        <el-input
          v-model="result"
          type="textarea"
          :rows="target.key === 'description' ? 8 : 4"
          :placeholder="loading ? 'AI 正在生成，请稍候…' : '点击右下角「生成」后在此预览，可手动编辑后再应用'"
        />
        <div v-if="meta" class="ai-meta">
          trace: {{ meta.trace_id }} ｜ {{ meta.model }} ｜ {{ meta.tokens }} tokens ｜ {{ meta.duration_ms }}ms
        </div>
      </el-form-item>
    </el-form>
    <div slot="footer">
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button size="small" type="primary" plain :loading="loading" @click="generate">
        {{ result ? '重新生成' : '生成' }}
      </el-button>
      <el-button size="small" type="primary" :disabled="!result || loading" @click="apply">应用</el-button>
    </div>
  </el-dialog>
</template>

<script>
import { aiCopywriting } from '@/api/ai';

export default {
  name: 'AiCopywriting',
  data() {
    return {
      visible: false,
      loading: false,
      target: {},
      tone: 'professional',
      seed: '',
      result: '',
      meta: null,
    };
  },
  methods: {
    /**
     * 打开弹窗
     * @param {Object} target { key, label, kind }（key 为 formValidate 字段名）
     * @param {Object} ctx { seed, context } 当前字段值 / 商品上下文
     */
    open(target, ctx = {}) {
      this.target = target || {};
      this.seed = ctx.seed || ctx.context || '';
      this.result = '';
      this.meta = null;
      this.visible = true;
    },
    generate() {
      const seed = (this.seed || '').trim();
      if (!seed) {
        this.$message.error('请先填写生成素材');
        return;
      }
      this.loading = true;
      aiCopywriting({ kind: this.target.kind, seed, tone: this.tone })
        .then((res) => {
          const d = res.data || {};
          this.result = d.text || '';
          this.meta = {
            trace_id: d.trace_id,
            model: d.model,
            tokens: d.tokens,
            duration_ms: d.duration_ms,
          };
        })
        .catch((err) => {
          this.$message.error((err && err.msg) || 'AI 生成失败，请稍后重试');
        })
        .finally(() => {
          this.loading = false;
        });
    },
    apply() {
      const text = (this.result || '').trim();
      if (!text) return;
      this.$emit('apply', { key: this.target.key, text });
      this.visible = false;
    },
  },
};
</script>
<style lang="scss" scoped>
.ai-meta {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
  word-break: break-all;
}
</style>
