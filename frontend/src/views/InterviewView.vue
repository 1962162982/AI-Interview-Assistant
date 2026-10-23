<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { SAMPLE_JD_TEXT, SAMPLE_RESUME_TEXT } from '../data/samples'
import { addSession, getResume } from '../stores/history'

// 模块 C 模拟面试：真实调用 POST /sessions、/turns/stream(SSE)、/{sid}/end（契约 docs/api.md「五」）。
// - SSE：delta 增量渲染、done 更新题目 / 状态、error 事件友好提示（client.js 抛 ApiError）；
// - 防重复提交 + 加载态 + 超时（client.js 统一处理）+ 可中途停止生成（AbortController）；
// - 三场景均实现：intern（实习，默认）/ autumn（秋招）/ grad（保研）。
const SCENE_LABELS = { intern: '实习', autumn: '秋招', grad: '保研' }
const scenes = [
  { value: 'intern', label: '实习' },
  { value: 'autumn', label: '秋招' },
  { value: 'grad', label: '保研' },
]

const router = useRouter()
const storedResume = getResume()
const resumeText = ref(storedResume?.text || SAMPLE_RESUME_TEXT)
const scene = ref('intern')
const phase = ref('idle') // idle | creating | chatting | ending
const errorMessage = ref('')
const notice = ref('')
const sessionId = ref('')
const messages = ref([])
const answer = ref('')
const streaming = ref(false)
const currentQuestion = ref(null)
const sessionState = ref(null)
let streamController = null
let timerId = null
const interviewStartedAt = ref(0)
const elapsedSeconds = ref(0)
const clockText = computed(() => {
  const total = Math.max(0, elapsedSeconds.value)
  const mm = String(Math.floor(total / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

function stopTimer() {
  if (timerId) {
    clearInterval(timerId)
    timerId = null
  }
}

function startTimer() {
  stopTimer()
  interviewStartedAt.value = Date.now()
  elapsedSeconds.value = 0
  timerId = setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - interviewStartedAt.value) / 1000)
  }, 1000)
}

onUnmounted(() => {
  stopTimer()
  streamController?.abort()
})

const busy = computed(() => phase.value === 'creating' || phase.value === 'ending')
const canSend = computed(() => phase.value === 'chatting' && !streaming.value && answer.value.trim().length > 0)
const sceneLabel = computed(() => SCENE_LABELS[scene.value] || scene.value)
const resumeSource = computed(() =>
  storedResume ? '本地历史简历（简历诊断页保存）' : '示例简历（未检测到本地历史）',
)

async function startInterview() {
  if (busy.value || phase.value === 'chatting') return
  phase.value = 'creating'
  errorMessage.value = ''
  notice.value = ''
  messages.value = []
  sessionState.value = null
  currentQuestion.value = null
  try {
    const data = await api.createInterviewSession({
      resume_text: resumeText.value.trim(),
      jd_text: SAMPLE_JD_TEXT,
      scene: scene.value,
      company: '某科技公司',
      role: '后端开发实习生',
      style: 'friendly',
    })
    sessionId.value = data.session_id
    currentQuestion.value = data.question
    sessionState.value = data.state
    messages.value = [{ role: 'interviewer', text: data.question.text, streaming: false }]
    addSession({ sessionId: data.session_id, scene: scene.value, role: '后端开发实习生', company: '某科技公司' })
    notice.value = `会话已创建：${data.session_id}（已记录到本地历史）`
    phase.value = 'chatting'
    startTimer()
  } catch (error) {
    if (error.cancelled) notice.value = '已取消创建会话。'
    else errorMessage.value = error.message
    phase.value = 'idle'
  }
}

function stopStreaming() {
  streamController?.abort()
}

async function sendAnswer() {
  const text = answer.value.trim()
  if (!text || phase.value !== 'chatting' || streaming.value) return
  messages.value.push({ role: 'candidate', text, streaming: false })
  answer.value = ''
  const bubble = { role: 'interviewer', text: '', streaming: true }
  messages.value.push(bubble)
  streaming.value = true
  errorMessage.value = ''
  notice.value = ''
  streamController = new AbortController()
  try {
    const doneEvent = await api.streamAnswer(sessionId.value, text, {
      signal: streamController.signal,
      onEvent(event) {
        if (event.event === 'delta') {
          bubble.text += event.text ?? ''
        } else if (event.event === 'done') {
          currentQuestion.value = event.question || currentQuestion.value
          sessionState.value = { ...(sessionState.value || {}), ...(event.state || {}) }
          if (!bubble.text && event.question?.text) bubble.text = event.question.text
        }
      },
    })
    if (doneEvent?.event === 'done' && !bubble.text) {
      bubble.text = doneEvent.question?.text || ''
    }
  } catch (error) {
    if (error.cancelled) {
      if (!bubble.text) bubble.text = '（已停止生成）'
      notice.value = '已停止本次生成；可以重新发送回答继续面试。'
    } else {
      errorMessage.value = error.retryable ? `${error.message}（可重试）` : error.message
      if (!bubble.text) bubble.text = '（面试官暂时无法回复，请稍后重试）'
    }
  } finally {
    bubble.streaming = false
    streaming.value = false
    streamController = null
  }
}

async function endInterview() {
  if (!sessionId.value || phase.value !== 'chatting' || streaming.value) return
  phase.value = 'ending'
  errorMessage.value = ''
  try {
    await api.endSession(sessionId.value)
    stopTimer()
    notice.value = '面试已结束，正在跳转报告页…'
    router.push({ path: '/report', query: { session: sessionId.value } })
  } catch (error) {
    if (error.cancelled) notice.value = '已取消结束请求。'
    else errorMessage.value = error.message
    phase.value = 'chatting'
  }
}
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">模拟面试</p>
      <h2>会追问的模拟面试：像真实面试一样练</h2>
      <p class="lead">
        面试官会基于你的简历与目标岗位生成问题，并根据回答继续追问：
        回答含糊会追问数据来源，出现技术名词会深挖原理，连续追问过多会自动换方向。
      </p>
      <div class="chip-row">
        <span class="chip">三场景：实习 / 秋招 / 保研</span>
        <span class="chip">动态追问</span>
        <span class="chip">逐字流式对话</span>
      </div>
    </header>

    <div class="form-card">
      <h3 class="section-title">选择面试场景（简历来源：{{ resumeSource }}）</h3>
      <div class="radio-row">
        <label
          v-for="item in scenes"
          :key="item.value"
          class="radio-pill"
          :class="{ 'is-checked': scene === item.value }"
        >
          <input v-model="scene" type="radio" :value="item.value" :disabled="phase === 'chatting'" />
          {{ item.label }}
        </label>
      </div>
      <span class="hint">支持实习 / 秋招 / 保研三种场景，默认实习；不同场景的语气与问题侧重不同。</span>
      <div class="panel-actions">
        <button class="button" :disabled="busy || phase === 'chatting'" @click="startInterview">
          {{ phase === 'creating' ? '创建会话…' : phase === 'chatting' ? '面试进行中' : '开始面试' }}
        </button>
        <button class="button ghost" :disabled="phase !== 'chatting' || streaming" @click="endInterview">
          {{ phase === 'ending' ? '生成对话记录中…' : '结束面试并生成报告' }}
        </button>
        <span v-if="notice" class="status">{{ notice }}</span>
        <span v-if="errorMessage" class="status" data-state="error">{{ errorMessage }}</span>
      </div>
    </div>

    <div class="split-grid">
      <div class="form-card">
        <h3 class="section-title">对话区</h3>
        <div v-if="messages.length" class="chat-log">
          <div
            v-for="(message, index) in messages"
            :key="index"
            class="chat-bubble"
            :class="message.role === 'interviewer' ? 'interviewer' : 'candidate'"
          >
            <span class="chat-role">{{ message.role === 'interviewer' ? '面试官' : '我' }}</span>
            <p class="chat-text">{{ message.text }}<span v-if="message.streaming" class="cursor">▍</span></p>
          </div>
        </div>
        <div v-else class="chat-empty">
          会话未开始<br />
          点击「开始面试」后，面试官首问与多轮追问会逐字显示
        </div>

        <div class="field">
          <label for="answer">你的回答</label>
          <textarea
            id="answer"
            v-model="answer"
            class="textarea"
            rows="4"
            :disabled="phase !== 'chatting' || streaming"
            placeholder="用 STAR 结构回答：背景 → 目标 → 动作 → 结果数据"
          ></textarea>
        </div>
        <div class="panel-actions">
          <button class="button" :disabled="!canSend" @click="sendAnswer">
            {{ streaming ? '面试官输入中…' : '发送回答' }}
          </button>
          <button v-if="streaming" class="button ghost" @click="stopStreaming">停止生成</button>
          <span class="muted">第 {{ (sessionState?.turn_index ?? 0) + 1 }} 轮</span>
        </div>
      </div>

      <div class="stack">
        <div class="form-card">
          <h3 class="section-title">会话状态</h3>
          <ul class="placeholder-list">
            <li><span class="idx">场</span><span>场景：{{ sceneLabel }}（{{ scene }}）</span></li>
            <li><span class="idx">题</span><span>当前题目：{{ currentQuestion?.question_id || '--' }}</span></li>
            <li><span class="idx">深</span><span>追问深度：{{ currentQuestion?.follow_up_depth ?? '--' }}</span></li>
            <li><span class="idx">轮</span><span>已进行：{{ sessionState?.turn_index ?? 0 }} 轮</span></li>
            <li><span class="idx">时</span><span>面试用时：{{ clockText }}</span></li>
            <li><span class="idx">ID</span><span>会话号：<code>{{ sessionId || '--' }}</code></span></li>
          </ul>
        </div>
        <div v-if="currentQuestion" class="form-card">
          <h3 class="section-title">追问提示</h3>
          <p class="muted">当前追问方向：{{ currentQuestion.direction || '--' }}（追问深度 {{ currentQuestion.follow_up_depth ?? 0 }}）</p>
          <p class="muted">同一方向连续追问 ≥2 次会自动换方向，单方向深度上限 3，避免一直追问同一个点。</p>
        </div>
        <div class="form-card">
          <h3 class="section-title">长对话保障</h3>
          <p class="muted">系统会自动压缩较早的对话内容，保证 30 分钟左右的面试不中断。</p>
        </div>
      </div>
    </div>
  </section>
</template>
