<script setup>
import { computed, ref } from 'vue'
import { api, isMockMode } from '../api/client'
import { SAMPLE_RESUME_TEXT } from '../data/samples'
import { getResume, saveResume } from '../stores/history'

// 模块 A 简历诊断：真实调用 /api/resume/parse + /api/resume/diagnose（契约 docs/api.md「三」）。
// - 上传解析为本地解析，不消耗模型调用；诊断会调用模型（resume_diagnosis 路由）。
// - 上传/诊断均支持取消（AbortController），请求超时由 client.js 统一处理。
const PRIORITY_LABELS = { high: '高', medium: '中', low: '低' }
const checks = [
  { title: '量化表达', detail: '是否有可验证的数字、指标与结果' },
  { title: '动词弱化', detail: '「参与 / 负责」等弱动词是否可替换为具体动作' },
  { title: '关键词对齐', detail: '是否命中目标岗位 JD 的能力关键词' },
  { title: '结构问题', detail: '经历描述是否遵循 STAR / 结果导向结构' },
  { title: '错别字格式', detail: '标点、时间线、中英文混排等格式问题' },
]

const previousResume = getResume()
const resumeText = ref(previousResume?.text || '')
const targetRole = ref('后端开发实习生')
const uploadName = ref(previousResume?.filename || '')
const phase = ref('idle') // idle | parsing | analyzing | done
const errorMessage = ref('')
const notice = ref('')
const diagnosis = ref(null)
let activeController = null

const busy = computed(() => phase.value === 'parsing' || phase.value === 'analyzing')
const charCount = computed(() => resumeText.value.trim().length)

function cancelCurrent() {
  activeController?.abort()
}

async function onFileChange(event) {
  const file = event.target.files?.[0]
  if (!file) return
  phase.value = 'parsing'
  errorMessage.value = ''
  notice.value = ''
  const localController = new AbortController()
  activeController = localController
  try {
    const data = await api.resumeParse(file, { signal: localController.signal })
    resumeText.value = data?.text || ''
    uploadName.value = data?.filename || file.name
    if (data?.degraded) {
      notice.value = '解析质量有限（可能为扫描件 / 乱码），已降级为纯文本；请检查后手动修正，或改用「粘贴文本」通道。'
    } else {
      notice.value = `解析成功：${data?.char_count ?? resumeText.value.length} 字${data?.segments?.length ? `，${data.segments.length} 个分段` : ''}。`
      saveResume({ text: resumeText.value, filename: uploadName.value, source: 'upload' })
    }
    phase.value = 'idle'
  } catch (error) {
    if (error.cancelled) notice.value = '已取消解析请求。'
    else errorMessage.value = error.message
    phase.value = 'idle'
  } finally {
    activeController = null
    event.target.value = ''
  }
}

function fillSample() {
  resumeText.value = SAMPLE_RESUME_TEXT
  uploadName.value = ''
  diagnosis.value = null
  errorMessage.value = ''
  notice.value = '已填入虚构示例简历，可直接点击「开始诊断」。'
  phase.value = 'idle'
}

async function diagnose() {
  if (!resumeText.value.trim()) {
    errorMessage.value = '请先上传简历（PDF / DOCX）或粘贴简历文本'
    return
  }
  if (busy.value) return
  phase.value = 'analyzing'
  errorMessage.value = ''
  notice.value = ''
  const localController = new AbortController()
  activeController = localController
  try {
    diagnosis.value = await api.resumeDiagnose({
      resume_text: resumeText.value.trim(),
      target_role: targetRole.value.trim() || undefined,
    }, { signal: localController.signal })
    saveResume({
      text: resumeText.value,
      filename: uploadName.value,
      source: uploadName.value ? 'upload' : 'paste',
    })
    phase.value = 'done'
    notice.value = '诊断完成；结果已保存为本地历史，可在 JD 匹配 / 模拟面试中复用。'
  } catch (error) {
    if (error.cancelled) notice.value = '已取消诊断请求。'
    else errorMessage.value = error.message
    phase.value = 'idle'
  } finally {
    activeController = null
  }
}
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">模块 A · 简历诊断</p>
      <h2>解析简历，按 5 项检查清单给出可执行的改写建议</h2>
      <p class="lead">
        上传 PDF / DOCX 或粘贴纯文本 → 解析与脱敏 → 结构化抽取（ResumeStructured）→
        输出问题清单、优先级、原文引用与改写示例。结构化简历是后续 JD 匹配、模拟面试与评分的公共依赖。
      </p>
      <div class="chip-row">
        <span class="chip">5 项检查清单</span>
        <span class="chip">强制引用原文</span>
        <span class="chip">few-shot 改写</span>
        <span v-if="isMockMode" class="chip chip-accent">Mock 数据模式</span>
        <span v-else class="chip chip-live">真实后端模式</span>
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <div class="field">
          <label for="resume-file">上传简历（PDF / DOCX）</label>
          <div class="upload-zone">
            <strong>{{ uploadName || '选择文件后自动解析' }}</strong>
            <span>调用 <code>POST /api/resume/parse</code>（本地解析，不调用模型）；扫描件 / 乱码返回降级标记。</span>
            <div class="inline-actions">
              <label class="button file-button">
                {{ phase === 'parsing' ? '解析中…' : '选择文件' }}
                <input id="resume-file" type="file" accept=".pdf,.docx" :disabled="busy" @change="onFileChange" />
              </label>
              <button class="button ghost" :disabled="busy" @click="fillSample">填入示例简历</button>
            </div>
          </div>
        </div>

        <div class="field">
          <label for="resume-text">简历文本（可粘贴或直接修改）</label>
          <textarea
            id="resume-text"
            v-model="resumeText"
            class="textarea"
            rows="9"
            placeholder="粘贴纯文本简历，或点击上方「填入示例简历」"
          ></textarea>
          <span class="hint">当前 {{ charCount }} 字；上传成功或诊断后会保存到本地历史（localStorage）。</span>
        </div>

        <div class="field">
          <label for="target-role">目标岗位（可选，用于「关键词对齐」检查）</label>
          <input id="target-role" v-model="targetRole" class="input" type="text" placeholder="例如：后端开发实习生" />
        </div>

        <div v-if="notice" class="notice">{{ notice }}</div>
        <div v-if="errorMessage" class="notice notice-error">{{ errorMessage }}</div>

        <div class="panel-actions">
          <button class="button" :disabled="busy" @click="diagnose">
            {{ phase === 'analyzing' ? '诊断中…' : '开始诊断' }}
          </button>
          <button v-if="busy" class="button ghost" @click="cancelCurrent">取消请求</button>
          <span v-if="busy" class="muted">诊断会调用后端模型，通常需要 10–60 秒。</span>
        </div>
      </div>

      <div class="stack">
        <div class="form-card">
          <h3 class="section-title">5 项检查清单</h3>
          <ul class="placeholder-list">
            <li v-for="(check, index) in checks" :key="check.title">
              <span class="idx">{{ index + 1 }}</span>
              <span><strong>{{ check.title }}</strong>：{{ check.detail }}</span>
            </li>
          </ul>
        </div>

        <div v-if="diagnosis" class="form-card">
          <h3 class="section-title">诊断报告</h3>
          <p class="muted">{{ diagnosis.summary }}</p>
          <div class="issue-list">
            <article v-for="(issue, index) in diagnosis.issues" :key="index" class="issue-card">
              <header class="issue-head">
                <span class="badge">{{ issue.check_item }}</span>
                <span class="priority" :class="`priority-${issue.priority}`">优先级：{{ PRIORITY_LABELS[issue.priority] || issue.priority }}</span>
              </header>
              <blockquote class="quote">原文：{{ issue.original_quote }}</blockquote>
              <p class="issue-line"><strong>问题：</strong>{{ issue.problem }}</p>
              <p class="issue-line"><strong>改写：</strong>{{ issue.rewrite }}</p>
            </article>
          </div>
          <div>
            <h4 class="sub-title">亮点</h4>
            <ul class="plain-list">
              <li v-for="item in diagnosis.strengths" :key="item">{{ item }}</li>
            </ul>
          </div>
        </div>
        <div v-else class="form-card">
          <h3 class="section-title">诊断报告</h3>
          <div class="placeholder-block">点击「开始诊断」后，在此展示问题清单 + 优先级 + 原文证据 + 改写示例</div>
        </div>
      </div>
    </div>
  </section>
</template>
