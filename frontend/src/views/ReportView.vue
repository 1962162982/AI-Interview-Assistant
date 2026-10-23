<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/client'
import { addReport, formatTimestamp, getLatestReport, getLatestSession, loadHistory } from '../stores/history'

// 模块 D 即时反馈：真实调用 POST /api/report/generate 或 GET /api/report/{rid}（契约 docs/api.md「五」）。
// - 生成报告 = 评分（evaluation）+ 交叉复核（cross_review），是 2 次模型调用，耗时较长；
//   因此真实模式下不会在页面加载时自动生成，需手动点击确认（成本 / 时间可控）。
// - 导出：优先用生成响应里的 markdown，否则 GET /api/report/{rid}/markdown 下载。
const route = useRoute()
const report = ref(null)
const phase = ref('idle') // idle | loading | done | error
const loadingKind = ref('') // generate | fetch
const errorMessage = ref('')
const exportError = ref('')
const sessionInput = ref('')
const reportInput = ref('')
const historyState = ref(loadHistory())
let activeController = null

const busy = computed(() => phase.value === 'loading')
const canGenerate = computed(() => !busy.value && sessionInput.value.trim().length > 0)
const canFetch = computed(() => !busy.value && reportInput.value.trim().length > 0)
const loadingHint = computed(() => {
  if (loadingKind.value === 'generate') return '正在生成报告：逐题评分 + 1 次交叉复核（≥2 次模型调用），约 30–90 秒…'
  if (loadingKind.value === 'fetch') return '正在拉取报告…'
  return '加载中…'
})

function refreshHistory() {
  historyState.value = loadHistory()
}

function cancelCurrent() {
  activeController?.abort()
}

async function generateReport() {
  if (!canGenerate.value) return
  phase.value = 'loading'
  loadingKind.value = 'generate'
  errorMessage.value = ''
  const localController = new AbortController()
  activeController = localController
  try {
    report.value = await api.generateReport(
      { session_id: sessionInput.value.trim() },
      { signal: localController.signal },
    )
    if (report.value?.report_id) {
      addReport({ reportId: report.value.report_id, sessionId: sessionInput.value.trim() })
    }
    phase.value = 'done'
    refreshHistory()
  } catch (error) {
    if (error.cancelled) {
      phase.value = 'idle'
      errorMessage.value = '已取消报告生成。'
    } else {
      phase.value = 'error'
      errorMessage.value = error.retryable ? `${error.message}（可重试）` : error.message
    }
  } finally {
    activeController = null
  }
}

async function fetchReport() {
  if (!canFetch.value) return
  phase.value = 'loading'
  loadingKind.value = 'fetch'
  errorMessage.value = ''
  const localController = new AbortController()
  activeController = localController
  try {
    report.value = await api.getReport(reportInput.value.trim(), { signal: localController.signal })
    if (report.value?.report_id) {
      addReport({ reportId: report.value.report_id, sessionId: report.value.session_id || '' })
    }
    phase.value = 'done'
    refreshHistory()
  } catch (error) {
    if (error.cancelled) {
      phase.value = 'idle'
      errorMessage.value = '已取消拉取请求。'
    } else {
      phase.value = 'error'
      errorMessage.value = error.message
    }
  } finally {
    activeController = null
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

onMounted(() => {
  const query = route.query
  if (query.report) {
    reportInput.value = String(query.report)
    fetchReport()
  } else if (query.session) {
    // 从面试页跳转过来时预填会话号，由用户确认后再生成报告（避免自动产生模型调用费用）
    sessionInput.value = String(query.session)
  } else {
    const latestSession = getLatestSession()
    if (latestSession) sessionInput.value = latestSession.sessionId
    const latestReport = getLatestReport()
    if (latestReport) reportInput.value = latestReport.reportId
  }
})
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">反馈报告</p>
      <h2>可解释的面试反馈：维度分 + 原文证据 + 交叉复核</h2>
      <p class="lead">
        评分从 4 个维度给出（各 1–10 分），并要求先引用你的原话作为证据；证据不足时该维度分数会被限制。
        面试结束后还会由另一个模型交叉复核，报告可导出为 Markdown。
      </p>
      <div class="chip-row">
        <span class="chip">4 维度评分</span>
        <span class="chip">原文证据</span>
        <span class="chip">交叉复核</span>
        <span class="chip">Markdown 导出</span>
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <h3 class="section-title">生成新报告</h3>
        <div class="field">
          <label for="session-id">面试会话号</label>
          <input id="session-id" v-model="sessionInput" class="input" type="text" placeholder="例如 sess_20261023_ab12cd" :disabled="busy" />
          <span class="hint">报告需要逐题评分与交叉复核，通常需要 1–2 分钟。</span>
        </div>
        <div class="panel-actions">
          <button class="button" :disabled="!canGenerate" @click="generateReport">
            {{ loadingKind === 'generate' && busy ? '生成中…' : '生成报告' }}
          </button>
        </div>
      </div>

      <div class="form-card">
        <h3 class="section-title">查看历史报告</h3>
        <div class="field">
          <label for="report-id">报告编号</label>
          <input id="report-id" v-model="reportInput" class="input" type="text" placeholder="例如 rep_20261023_x1y2z3" :disabled="busy" />
          <span class="hint">仅读取已生成的报告，不会重新评分。</span>
        </div>
        <div class="panel-actions">
          <button class="button" :disabled="!canFetch" @click="fetchReport">
            {{ loadingKind === 'fetch' && busy ? '拉取中…' : '拉取报告' }}
          </button>
        </div>
      </div>
    </div>

    <div class="form-card">
      <h3 class="section-title">最近记录</h3>
      <p class="muted">
        最近面试：<code>{{ historyState.sessions[0]?.sessionId || '--' }}</code>
        （{{ historyState.sessions[0] ? formatTimestamp(historyState.sessions[0].createdAt) : '暂无' }}） ·
        最近报告：<code>{{ historyState.reports[0]?.reportId || '--' }}</code>
        （{{ historyState.reports[0] ? formatTimestamp(historyState.reports[0].createdAt) : '暂无' }}）
      </p>
    </div>

    <div v-if="busy" class="form-card">
      <div class="notice">{{ loadingHint }}</div>
      <div class="panel-actions">
        <button class="button ghost" @click="cancelCurrent">取消请求</button>
      </div>
    </div>
    <div v-else-if="phase === 'error'" class="form-card">
      <div class="notice notice-error">{{ errorMessage }}</div>
      <div class="panel-actions">
        <button class="button" :disabled="!canGenerate" @click="generateReport">重新生成</button>
        <button class="button ghost" :disabled="!canFetch" @click="fetchReport">重新拉取</button>
      </div>
    </div>

    <template v-else-if="report">
      <div class="metric-grid">
        <div v-for="item in report.evaluation?.dimensions ?? []" :key="item.dimension" class="metric">
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
            <ul v-if="report.evaluation?.strengths?.length" class="plain-list">
              <li v-for="item in report.evaluation.strengths" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="muted">暂无数据。</p>
          </div>
          <div class="form-card">
            <h3 class="section-title">短板</h3>
            <ul v-if="report.evaluation?.weaknesses?.length" class="plain-list">
              <li v-for="item in report.evaluation.weaknesses" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="muted">暂无数据。</p>
          </div>
          <div class="form-card">
            <h3 class="section-title">改进建议</h3>
            <ul v-if="report.evaluation?.suggestions?.length" class="plain-list">
              <li v-for="item in report.evaluation.suggestions" :key="item">{{ item }}</li>
            </ul>
            <p v-else class="muted">暂无数据。</p>
          </div>
        </div>

        <div class="stack">
          <div class="form-card">
            <h3 class="section-title">交叉复核</h3>
            <p class="muted">
              结论：{{ report.cross_review?.agreed ? '复核结果与评分一致' : '复核结果有调整建议' }}；
              说明：{{ report.cross_review?.deviation_note || '暂无' }}
            </p>
            <ul v-if="report.cross_review?.adjustments?.length" class="plain-list">
              <li v-for="(item, index) in report.cross_review.adjustments" :key="index">{{ item }}</li>
            </ul>
          </div>
          <div class="form-card">
            <h3 class="section-title">导出报告</h3>
            <p class="muted">报告编号：<code>{{ report.report_id }}</code></p>
            <div class="panel-actions">
              <button class="button" @click="downloadMarkdown">下载 Markdown</button>
            </div>
            <span v-if="exportError" class="status" data-state="error">{{ exportError }}</span>
          </div>
          <div class="form-card">
            <h3 class="section-title">报告摘要</h3>
            <pre v-if="report.markdown" class="markdown-preview">{{ report.markdown }}</pre>
            <p v-else class="muted">该报告暂无 Markdown 内容；点击「下载 Markdown」会向后端请求导出。</p>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="form-card">
      <div class="placeholder-block">
        输入面试会话号生成报告，或输入报告编号查看历史报告；完成后在此展示维度分、原文证据与交叉复核。
      </div>
    </div>
  </section>
</template>
