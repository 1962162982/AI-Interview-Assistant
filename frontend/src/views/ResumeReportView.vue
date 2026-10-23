<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { formatTimestamp, getResumeDiagnosis } from '../stores/history'

// 独立的「简历诊断报告」页面：诊断成功后在 ResumeView 保存报告 → 跳转到本页，
// 用整页宽度展示问题清单 / 优先级 / 原文证据 / 改写建议，便于阅读和对照修改。
const router = useRouter()
const payload = getResumeDiagnosis()
const report = payload?.report || null
const notice = ref('')

const PRIORITY_LABELS = { high: '高', medium: '中', low: '低' }
const PRIORITY_ORDER = ['high', 'medium', 'low']

const issues = computed(() => (Array.isArray(report?.issues) ? report.issues : []))
const strengths = computed(() => (Array.isArray(report?.strengths) ? report.strengths : []))
const groups = computed(() =>
  PRIORITY_ORDER.map((priority) => ({
    priority,
    label: PRIORITY_LABELS[priority],
    items: issues.value.filter((issue) => (issue.priority || 'medium') === priority),
  })).filter((group) => group.items.length > 0),
)

async function copyRewrite(text) {
  const content = String(text || '').trim()
  if (!content) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(content)
      notice.value = '改写建议已复制到剪贴板。'
      return
    }
  } catch {
    // 浏览器不支持/未授权时退化为提示
  }
  notice.value = '当前浏览器不支持自动复制，请手动选择文本复制。'
}
</script>

<template>
  <section class="page report-page">
    <header class="page-header">
      <p class="eyebrow">简历诊断报告</p>
      <h2>你的简历诊断报告</h2>
      <p class="lead">
        按优先级从高到低查看问题、原文证据与可直接参考的改写建议；改写为示例，请结合真实经历调整。
      </p>
      <div v-if="report" class="report-meta">
        <span>目标岗位：{{ payload.targetRole || '未指定' }}</span>
        <span>生成时间：{{ formatTimestamp(payload.savedAt) }}</span>
        <span>问题 {{ issues.length }} 条 · 亮点 {{ strengths.length }} 条</span>
      </div>
      <div class="panel-actions">
        <button class="button" @click="router.push('/resume')">重新诊断</button>
        <button class="button ghost" @click="router.push('/jd')">继续 JD 匹配</button>
      </div>
    </header>

    <div v-if="!report" class="form-card">
      <h3 class="section-title">还没有诊断报告</h3>
      <p class="muted">请先在「简历诊断」页上传或粘贴简历并点击「开始诊断」。</p>
      <div class="panel-actions">
        <button class="button" @click="router.push('/resume')">去简历诊断</button>
      </div>
    </div>

    <template v-else>
      <div class="form-card report-summary">
        <h3 class="section-title">总体评价</h3>
        <p>{{ report.summary || '（无总体评价）' }}</p>
        <template v-if="strengths.length">
          <h4 class="sub-title">简历亮点</h4>
          <ul class="plain-list">
            <li v-for="item in strengths" :key="item">{{ item }}</li>
          </ul>
        </template>
      </div>

      <section v-for="group in groups" :key="group.priority" class="report-section">
        <h3 class="section-title">
          <span class="priority" :class="`priority-${group.priority}`">{{ group.label }}优先级问题</span>
          <span class="muted">共 {{ group.items.length }} 条</span>
        </h3>
        <div class="issue-grid">
          <article v-for="(issue, index) in group.items" :key="`${group.priority}-${index}`" class="issue-card">
            <header class="issue-head">
              <span class="badge">{{ issue.check_item }}</span>
              <span class="priority" :class="`priority-${issue.priority || 'medium'}`">
                优先级：{{ PRIORITY_LABELS[issue.priority] || '中' }}
              </span>
            </header>
            <blockquote v-if="issue.original_quote" class="quote">原文：{{ issue.original_quote }}</blockquote>
            <p class="issue-line"><strong>问题：</strong>{{ issue.problem }}</p>
            <div class="rewrite-block">
              <p class="issue-line"><strong>改写建议：</strong>{{ issue.rewrite }}</p>
              <button class="button ghost small" @click="copyRewrite(issue.rewrite)">复制</button>
            </div>
          </article>
        </div>
      </section>

      <div v-if="notice" class="notice">{{ notice }}</div>

      <div class="form-card">
        <h3 class="section-title">下一步怎么改</h3>
        <p class="muted">
          先处理「高优先级」问题，再补充量化数据与岗位关键词；改写示例只做参考，请务必使用真实经历，
          不要编造项目或数据。修改后可以回到「简历诊断」重新检查一次。
        </p>
        <div class="panel-actions">
          <button class="button" @click="router.push('/resume')">返回简历诊断</button>
          <button class="button ghost" @click="router.push('/jd')">继续 JD 匹配</button>
        </div>
      </div>
    </template>
  </section>
</template>
