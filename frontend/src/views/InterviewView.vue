<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, isMockMode } from '../api/client'
import { SAMPLE_JD_TEXT, SAMPLE_RESUME_TEXT } from '../api/mock'

// 模块 C 模拟面试：按 docs/api.md 契约调用 sessions / turns/stream(SSE) / end。
// 三场景均实现：intern（实习，默认）/ autumn（秋招）/ grad（保研）。
const SCENE_LABELS = { intern: '实习', autumn: '秋招', grad: '保研' }
const scenes = [
  { value: 'intern', label: '实习' },
  { value: 'autumn', label: '秋招' },
  { value: 'grad', label: '保研' },
]

const router = useRouter()
const scene = ref('intern')
const phase = ref('idle') // idle | creating | chatting | ending
const errorMessage = ref('')
const sessionId = ref('')
const messages = ref([])
const answer = ref('')
const streaming = ref(false)
const currentQuestion = ref(null)
const sessionState = ref(null)

const busy = computed(() => phase.value === 'creating' || phase.value === 'ending')
const canSend = computed(() => phase.value === 'chatting' && !streaming.value && answer.value.trim().length > 0)
const sceneLabel = computed(() => SCENE_LABELS[scene.value] || scene.value)

async function startInterview() {
  phase.value = 'creating'
  errorMessage.value = ''
  messages.value = []
  sessionState.value = null
  currentQuestion.value = null
  try {
    const data = await api.createInterviewSession({
      resume_text: SAMPLE_RESUME_TEXT,
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
    phase.value = 'chatting'
  } catch (error) {
    errorMessage.value = error.message
    phase.value = 'idle'
  }
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
  try {
    const doneEvent = await api.streamAnswer(sessionId.value, text, {
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
    errorMessage.value = error.message
    if (!bubble.text) bubble.text = '（面试官暂时无法回复，请稍后重试）'
  } finally {
    bubble.streaming = false
    streaming.value = false
  }
}

async function endInterview() {
  if (!sessionId.value) return
  phase.value = 'ending'
  errorMessage.value = ''
  try {
    await api.endSession(sessionId.value)
    router.push({ path: '/report', query: { session: sessionId.value } })
  } catch (error) {
    errorMessage.value = error.message
    phase.value = 'chatting'
  }
}
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">模块 C · 模拟面试</p>
      <h2>会追问的模拟面试：角色卡 + 追问决策树 + 三场景</h2>
      <p class="lead">
        面试官基于结构化简历与 JD 生成首问，并根据回答按显式追问决策树动态追问：
        回答含糊→追究数据来源；出现技术名词→深挖原理；与 JD 冲突→温和质疑；连续追问 ≥2 次→强制换向。
      </p>
      <div class="chip-row">
        <span class="chip">三场景：实习 / 秋招 / 保研</span>
        <span class="chip">追问决策树</span>
        <span class="chip">SSE 流式输出</span>
        <span v-if="isMockMode" class="chip chip-accent">Mock 数据模式</span>
      </div>
    </header>

    <div class="form-card">
      <h3 class="section-title">选择面试场景</h3>
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
      <span class="hint">三场景均在契约中实现（intern / autumn / grad），默认实习场景；不同场景语气与问题侧重不同。</span>
      <div class="panel-actions">
        <button class="button" :disabled="busy || phase === 'chatting'" @click="startInterview">
          {{ phase === 'creating' ? '创建会话…' : phase === 'chatting' ? '面试进行中' : '开始面试' }}
        </button>
        <button class="button ghost" :disabled="phase !== 'chatting'" @click="endInterview">
          {{ phase === 'ending' ? '生成报告中…' : '结束面试并生成报告' }}
        </button>
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
          点击「开始面试」后，面试官首问与多轮追问将以流式（SSE）逐字显示
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
            <li><span class="idx">量</span><span>上下文占用：{{ sessionState?.context_usage_pct ?? 0 }}%</span></li>
          </ul>
        </div>
        <div v-if="currentQuestion" class="form-card">
          <h3 class="section-title">当前追问方向</h3>
          <p class="muted">direction：<code>{{ currentQuestion.direction }}</code></p>
          <p class="muted">follow_up_depth：{{ currentQuestion.follow_up_depth }}</p>
          <p class="muted">命中追问决策树后，后端会返回对应 rule_id / direction，便于联调核对。</p>
        </div>
        <div class="form-card">
          <h3 class="section-title">上下文管理</h3>
          <p class="muted">每 5 轮滚动摘要 + 接近 90% 窗口阈值自动压缩，保证 30 分钟面试不中断（context_manager）。</p>
        </div>
      </div>
    </div>
  </section>
</template>
