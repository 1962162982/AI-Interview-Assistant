<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, isMockMode } from '../api/client'

// 模块 D 即时反馈：按 docs/api.md 契约调用 /api/report/generate。
// 报告结构 = InterviewEvaluation（维度分 + 证据 + 亮点 / 短板 / 建议）+ 交叉复核 + Markdown。
const route = useRoute()
const report = ref(null)
const phase = ref('idle') // idle | loading | done | error
const errorMessage = ref('')
const exportError = ref('')

async function loadReport() {
  phase.value = 'loading'
  errorMessage.value = ''
  try {
    report.value = await api.generateReport({
      session_id: String(route.query.session || 'sess_mock_demo'),
    })
    phase.value = 'done'
  } catch (error) {
    errorMessage.value = error.message
    phase.value = 'error'
  }
}

async function downloadMarkdown() {
  if (!report.value) return
  exportError.value = ''
  try {
    const markdown = report.value.markdown || (await api.getReportMarkdown(report.value.report_id))
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `report_${report.value.report_id}.md`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    exportError.value = error.message
  }
}

onMounted(loadReport)
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">模块 D · 反馈报告</p>
      <h2>可解释的面试反馈：维度分 + 原文证据 + 交叉复核</h2>
      <p class="lead">
        评分遵循 4 维度 rubric（各 1–10 分），并强制先引用原文证据再给分；证据不足两条时该维度不得高于 6 分。
        面试结束时由另一供应商模型交叉复核，报告包含偏差说明，可导出 Markdown。
      </p>
      <div class="chip-row">
        <span class="chip">4 维度 rubric</span>
        <span class="chip">强制 grounding</span>
        <span class="chip">交叉复核</span>
        <span class="chip">Markdown 导出</span>
        <span v-if="isMockMode" class="chip chip-accent">Mock 数据模式</span>
      </div>
    </header>

    <div v-if="phase === 'loading'" class="form-card">
      <div class="placeholder-block">正在生成报告…</div>
    </div>
    <div v-else-if="phase === 'error'" class="form-card">
      <div class="placeholder-block">{{ errorMessage }}</div>
      <div class="panel-actions">
        <button class="button" @click="loadReport">重新生成</button>
      </div>
    </div>

    <template v-else-if="report">
      <div class="metric-grid">
        <div v-for="item in report.evaluation.dimensions" :key="item.dimension" class="metric">
          <div class="label">{{ item.dimension }}</div>
          <div class="value">{{ item.score }}<small>/ 10</small></div>
          <ul class="evidence-list">
            <li v-for="evidence in item.evidence" :key="evidence">{{ evidence }}</li>
          </ul>
        </div>
      </div>

      <div class="split-grid">
        <div class="stack">
          <div class="form-card">
            <h3 class="section-title">亮点</h3>
            <ul class="plain-list">
              <li v-for="item in report.evaluation.strengths" :key="item">{{ item }}</li>
            </ul>
          </div>
          <div class="form-card">
            <h3 class="section-title">短板</h3>
            <ul class="plain-list">
              <li v-for="item in report.evaluation.weaknesses" :key="item">{{ item }}</li>
            </ul>
          </div>
          <div class="form-card">
            <h3 class="section-title">改进建议</h3>
            <ul class="plain-list">
              <li v-for="item in report.evaluation.suggestions" :key="item">{{ item }}</li>
            </ul>
          </div>
        </div>

        <div class="stack">
          <div class="form-card">
            <h3 class="section-title">交叉复核</h3>
            <p class="muted">
              结论：{{ report.cross_review.agreed ? '复核模型认可首评' : '复核模型有调整建议' }}；
              说明：{{ report.cross_review.deviation_note }}
            </p>
            <ul v-if="report.cross_review.adjustments?.length" class="plain-list">
              <li v-for="(item, index) in report.cross_review.adjustments" :key="index">{{ item }}</li>
            </ul>
          </div>
          <div class="form-card">
            <h3 class="section-title">导出报告</h3>
            <p class="muted">报告 ID：<code>{{ report.report_id }}</code>（报告格式消费 InterviewEvaluation）。</p>
            <div class="panel-actions">
              <button class="button" @click="downloadMarkdown">下载 Markdown</button>
              <button class="button ghost" :disabled="phase === 'loading'" @click="loadReport">重新生成</button>
            </div>
            <span v-if="exportError" class="status" data-state="error">{{ exportError }}</span>
          </div>
          <div class="form-card">
            <h3 class="section-title">Markdown 预览</h3>
            <pre class="markdown-preview">{{ report.markdown }}</pre>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>
