<template>
  <!-- 商品详情 -->
  <el-row>
    <el-col :span="16">
      <el-form-item label="商品详情：">
        <el-button
          size="small"
          type="primary"
          plain
          icon="el-icon-magic-stick"
          class="ai-detail-btn"
          v-db-click
          @click="$emit('aiGenerate', 'description')"
          >AI 生成详情</el-button
        >
        <WangEditor style="width: 100%" :content="contents" @editorContent="getEditorContent"></WangEditor>
      </el-form-item>
    </el-col>
    <el-col :span="6" style="width: 33%">
      <div class="ifam">
        <div class="content" v-html="content"></div>
      </div>
    </el-col>
  </el-row>
</template>

<script>
import WangEditor from '@/components/wangEditor/index.vue';

export default {
  name: 'ProductDetail',
  components: {
    WangEditor,
  },
  props: {
    contents: {
      type: String,
      required: true,
    },
    content: {
      type: String,
      required: true,
    },
  },
  methods: {
    getEditorContent(val) {
      this.$emit('getEditorContent', val);
    },
  },
};
</script>
<style lang="scss" scoped>
@use '../productAdd.scss' as *;
.ai-detail-btn {
  margin-bottom: 10px;
}
</style>
