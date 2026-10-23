<script setup>
import { computed, ref } from 'vue'
import { api, isMockMode } from '../api/client'
import { SAMPLE_RESUME_TEXT } from '../api/mock'

// 模块 A 简历诊断：按 docs/api.md 契约调用 /api/resume/parse + /api/resume/diagnose。
// Mock 模式（默认）返回同构虚构数据；VITE_USE_MOCK=false 时走真实后端。
const PRIORITY_LABELS = { high: '高', medium: '中', low: '低' }
const checks = [
  { title: '量化表达', detail: '是否有可验证的数字、指标与结果' },
  { title: '动词弱化', detail: '「参与 / 负责」等弱动词是否可替换为具体动作' },
  { title: '关键词对齐', detail: '是否命中目标岗位 JD 的能力关键词' },
  { title: '结构问题', detail: '经历描述是否遵循 STAR / 结果导向结构' },
  { title: '错别字格式', detail: '标点、时间线、中英文混排等格式问题' },
]

const resumeText = ref('')
const targetRole = ref('后端开发实习生')
const uploadName = ref('')
const phase = ref('idle') // idle | parsing | analyzing | done
const errorMessage = ref('')
const diagnosis = ref(null)

const busy = computed(() => phase.value === 'parsing' || phase.value === 'analyzing')

async function onFileChange(event) {
  const file = event.target.files?.[0]
  if (!file) return
  phase.value = 'parsing'
  errorMessage.value = ''
  try {
    const data = await api.resumeParse(file)
    resumeText.value = data?.text || ''
    uploadName.value = data?.filename || file.name
    if (data?.degraded) {
      errorMessage.value = '解析质量有限（可能为扫描件），已降级为纯文本，请检查后手动修正。'
    }
    phase.value = 'idle'
  } catch (error) {
    errorMessage.value = error.message
    phase.value = 'idle'
  } finally {
    event.target.value = ''
  }
}

function fillSample() {
  resumeText.value = SAMPLE_RESUME_TEXT
  uploadName.value = ''
  diagnosis.value = null
  errorMessage.value = ''
  phase.value = 'idle'
}

async function diagnose() {
  if (!resumeText.value.trim()) {
    errorMessage.value = '请先上传简历（PDF / DOCX）或粘贴简历文本'
    return
  }
  phase.value = 'analyzing'
  errorMessage.value = ''
  try {
    diagnosis.value = await api.resumeDiagnose({
      resume_text: resumeText.value.trim(),
      target_role: targetRole.value.trim() || undefined,
    })
    phase.value = 'done'
  } catch (error) {
    errorMessage.value = error.message
    phase.value = 'idle'
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
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <div class="field">
          <label for="resume-file">上传简历（PDF / DOCX）</label>
          <div class="upload-zone">
            <strong>{{ uploadName || '文件上传占位' }}</strong>
            <span>调用 <code>POST /api/resume/parse</code>；扫描件 / 乱码会返回降级标记，提示改用粘贴文本。</span>
            <div class="inline-actions">
              <label class="button file-button">
                选择文件
                <input id="resume-file" type="file" accept=".pdf,.docx" :disabled="busy" @change="onFileChange" />
              </label>
              <button class="button ghost" :disabled="busy" @click="fillSample">填入示例简历</button>
            </div>
          </div>
        </div>

        <div class="field">
          <label for="resume-text">或粘贴简历文本（解析失败降级通道）</label>
          <textarea
            id="resume-text"
            v-model="resumeText"
            class="textarea"
            rows="9"
            placeholder="粘贴纯文本简历，或点击上方「填入示例简历」"
          ></textarea>
        </div>

        <div class="field">
          <label for="target-role">目标岗位（可选）</label>
          <input id="target-role" v-model="targetRole" class="input" type="text" placeholder="例如：后端开发实习生" />
        </div>

        <div class="panel-actions">
          <button class="button" :disabled="busy" @click="diagnose">
            {{ phase === 'parsing' ? '解析中…' : phase === 'analyzing' ? '诊断中…' : '开始诊断' }}
          </button>
          <span v-if="errorMessage" class="status" data-state="error">{{ errorMessage }}</span>
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
