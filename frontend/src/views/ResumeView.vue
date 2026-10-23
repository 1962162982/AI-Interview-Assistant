<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { SAMPLE_RESUME_TEXT } from '../data/samples'
import { formatTimestamp, getResume, getResumeDiagnosis, saveResume, saveResumeDiagnosis } from '../stores/history'

// 简历诊断：真实调用 /api/resume/parse + /api/resume/diagnose。
// 诊断完成后保存报告并跳转到独立的「诊断报告」页（/resume-report），避免在窄栏中查看长报告。
const checks = [
  { title: '量化表达', detail: '是否有可验证的数字、指标与结果' },
  { title: '动词弱化', detail: '「参与 / 负责」等弱动词是否可替换为具体动作' },
  { title: '关键词对齐', detail: '是否命中目标岗位 JD 的能力关键词' },
  { title: '结构问题', detail: '经历描述是否遵循 STAR / 结果导向结构' },
  { title: '错别字格式', detail: '标点、时间线、中英文混排等格式问题' },
]

const router = useRouter()
const previousResume = getResume()
const resumeText = ref(previousResume?.text || '')
const targetRole = ref('后端开发实习生')
const uploadName = ref(previousResume?.filename || '')
const phase = ref('idle') // idle | parsing | analyzing
const errorMessage = ref('')
const notice = ref('')
const lastReport = ref(getResumeDiagnosis())
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
    const report = await api.resumeDiagnose({
      resume_text: resumeText.value.trim(),
      target_role: targetRole.value.trim() || undefined,
    }, { signal: localController.signal })
    saveResume({
      text: resumeText.value,
      filename: uploadName.value,
      source: uploadName.value ? 'upload' : 'paste',
    })
    lastReport.value = saveResumeDiagnosis({
      report,
      targetRole: targetRole.value.trim(),
      resumeName: uploadName.value,
    })
    notice.value = '诊断完成，正在打开完整诊断报告…'
    phase.value = 'idle'
    router.push('/resume-report')
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
      <p class="eyebrow">简历诊断</p>
      <h2>让简历更有说服力：找到问题，并给出可执行的改写建议</h2>
      <p class="lead">
        上传 PDF / DOCX 或粘贴简历文本，系统会从量化表达、动词、岗位关键词、结构与格式五个方面检查，
        逐条给出原文证据与改写示例；完整报告将在新页面中全屏展示，便于对照修改。
      </p>
      <div class="chip-row">
        <span class="chip">5 项检查清单</span>
        <span class="chip">引用原文证据</span>
        <span class="chip">逐条改写建议</span>
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <div class="field">
          <label for="resume-file">上传简历（PDF / DOCX）</label>
          <div class="upload-zone">
            <strong>{{ uploadName || '选择文件后自动解析' }}</strong>
            <span>支持 PDF / DOCX；扫描件或乱码时可改用下方「粘贴文本」通道。</span>
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
          <span class="hint">当前 {{ charCount }} 字；诊断完成后会自动打开完整报告页。</span>
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
          <span v-if="busy" class="muted">诊断通常需要 10–60 秒，请耐心等待。</span>
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

        <div class="form-card">
          <h3 class="section-title">最近一次诊断</h3>
          <template v-if="lastReport?.report">
            <p class="muted">
              目标岗位：{{ lastReport.targetRole || '未指定' }} · {{ formatTimestamp(lastReport.savedAt) }}
            </p>
            <p class="muted">
              共 {{ lastReport.report.issues?.length || 0 }} 条问题、{{ lastReport.report.strengths?.length || 0 }} 条亮点。
            </p>
            <div class="panel-actions">
              <button class="button" @click="router.push('/resume-report')">查看完整诊断报告</button>
            </div>
          </template>
          <div v-else class="placeholder-block">
            点击「开始诊断」后，会在新页面打开完整诊断报告（全部问题 + 优先级 + 原文证据 + 改写建议）
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
