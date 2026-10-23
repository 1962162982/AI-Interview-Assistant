<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { SAMPLE_JD_TEXT, SAMPLE_RESUME_TEXT } from '../data/samples'
import { formatTimestamp, getJdMatch, getResume, saveJdMatch, saveResume } from '../stores/history'

// JD 匹配：真实调用 /api/jd/match；匹配成功后保存结果并跳转到独立的「JD 匹配报告」页。
const router = useRouter()
const storedResume = getResume()
const resumeText = ref(storedResume?.text || SAMPLE_RESUME_TEXT)
const resumeSource = computed(() =>
  storedResume
    ? `本地历史简历（${formatTimestamp(storedResume.savedAt)}）`
    : '示例简历（尚未在「简历诊断」页解析 / 粘贴）',
)
const jdText = ref('')
const phase = ref('idle') // idle | matching
const errorMessage = ref('')
const notice = ref('')
const lastMatch = ref(getJdMatch())
let activeController = null

const busy = computed(() => phase.value === 'matching')

const pipeline = [
  { step: '①', title: '拆解岗位要求', detail: '从 JD 中拆出职责与能力项，明确岗位真正看重什么' },
  { step: '②', title: '逐项对照简历', detail: '把简历能力与岗位要求逐项比对，得到匹配度与差距清单' },
]

function fillSampleJd() {
  jdText.value = SAMPLE_JD_TEXT
}

function useSampleResume() {
  resumeText.value = SAMPLE_RESUME_TEXT
  notice.value = '已切换为虚构示例简历。'
}

function cancelCurrent() {
  activeController?.abort()
}

async function match() {
  if (busy.value) return
  if (!resumeText.value.trim()) {
    errorMessage.value = '缺少简历文本：请先在「简历诊断」页解析 / 粘贴简历，或使用示例简历'
    return
  }
  if (!jdText.value.trim()) {
    errorMessage.value = '请先输入目标岗位 JD 文本'
    return
  }
  phase.value = 'matching'
  errorMessage.value = ''
  notice.value = ''
  const localController = new AbortController()
  activeController = localController
  try {
    const result = await api.jdMatch({
      resume_text: resumeText.value.trim(),
      jd_text: jdText.value.trim(),
    }, { signal: localController.signal })
    saveResume({ text: resumeText.value, source: 'paste' })
    lastMatch.value = saveJdMatch({
      result,
      resumeText: resumeText.value.trim(),
      jdText: jdText.value.trim(),
    })
    notice.value = '匹配完成，正在打开完整匹配报告…'
    phase.value = 'idle'
    router.push('/jd-report')
  } catch (error) {
    if (error.cancelled) notice.value = '已取消匹配请求。'
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
      <p class="eyebrow">JD 匹配</p>
      <h2>看懂岗位要求：匹配度、能力差距与补齐建议</h2>
      <p class="lead">
        输入目标岗位 JD，系统会把岗位要求拆解成具体能力项，再与你的简历逐项对照，
        给出 0–100 匹配度、差距清单和雷达图，帮你判断“还差什么、怎么补”。
      </p>
      <div class="chip-row">
        <span class="chip">0–100 匹配度</span>
        <span class="chip">逐项能力对比</span>
        <span class="chip">差距清单</span>
        <span class="chip">5 维雷达图</span>
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <div class="field">
          <label for="resume-input">简历文本（{{ resumeSource }}）</label>
          <textarea id="resume-input" v-model="resumeText" class="textarea" rows="5" placeholder="粘贴简历文本，或先在简历诊断页解析"></textarea>
          <span class="hint">
            可在简历诊断页保存后自动带入；
            <a class="link-inline" href="#" @click.prevent="useSampleResume">使用示例简历</a>
          </span>
        </div>

        <div class="field">
          <label for="jd-text">目标岗位 JD 文本</label>
          <textarea
            id="jd-text"
            v-model="jdText"
            class="textarea"
            rows="9"
            placeholder="粘贴岗位 JD，或点击下方「填入示例 JD」"
          ></textarea>
        </div>

        <div v-if="notice" class="notice">{{ notice }}</div>
        <div v-if="errorMessage" class="notice notice-error">{{ errorMessage }}</div>

        <div class="panel-actions">
          <button class="button" :disabled="busy" @click="match">
            {{ busy ? '匹配中…' : '开始匹配' }}
          </button>
          <button v-if="busy" class="button ghost" @click="cancelCurrent">取消请求</button>
          <button class="button ghost" :disabled="busy" @click="fillSampleJd">填入示例 JD</button>
          <span v-if="busy" class="muted">匹配通常需要 10–90 秒，请耐心等待。</span>
        </div>
      </div>

      <div class="stack">
        <div class="form-card">
          <h3 class="section-title">匹配是怎么算的</h3>
          <ul class="placeholder-list">
            <li v-for="item in pipeline" :key="item.title">
              <span class="idx">{{ item.step }}</span>
              <span><strong>{{ item.title }}</strong>：{{ item.detail }}</span>
            </li>
          </ul>
        </div>

        <div class="form-card">
          <h3 class="section-title">最近一次匹配</h3>
          <template v-if="lastMatch?.result">
            <p class="muted">时间：{{ formatTimestamp(lastMatch.savedAt) }}</p>
            <p class="muted">
              匹配度：{{ lastMatch.result.score }} / 100 · 能力项 {{ lastMatch.result.items?.length || 0 }} 项 ·
              差距 {{ lastMatch.result.gaps?.length || 0 }} 项
            </p>
            <div class="panel-actions">
              <button class="button" @click="router.push('/jd-report')">查看完整匹配报告</button>
            </div>
          </template>
          <div v-else class="placeholder-block">
            点击「开始匹配」后，会在新页面展示匹配度、能力雷达图、逐项对比与差距清单
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
