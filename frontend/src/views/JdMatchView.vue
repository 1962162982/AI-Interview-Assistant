<script setup>
import { computed, ref } from 'vue'
import { api, isMockMode } from '../api/client'
import { SAMPLE_JD_TEXT, SAMPLE_RESUME_TEXT } from '../data/samples'
import { formatTimestamp, getResume, saveResume } from '../stores/history'
import { buildRadarData } from '../utils/radar'

// 模块 B JD 匹配：真实调用 /api/jd/match（两段式由后端编排，契约 docs/api.md「四」）。
// 简历优先取本地历史（简历诊断页保存）；没有则回退示例简历并提示。
const STATUS_LABELS = { matched: '已覆盖', partial: '部分覆盖', missing: '缺失' }

const storedResume = getResume()
const resumeText = ref(storedResume?.text || SAMPLE_RESUME_TEXT)
const resumeSource = computed(() =>
  storedResume
    ? `本地历史简历（${formatTimestamp(storedResume.savedAt)}）`
    : '示例简历（尚未在「简历诊断」页解析 / 粘贴）',
)
const jdText = ref('')
const phase = ref('idle') // idle | matching | done
const errorMessage = ref('')
const notice = ref('')
const result = ref(null)
let activeController = null

const busy = computed(() => phase.value === 'matching')

// 雷达图：数据推导规则集中写在 src/utils/radar.js（纯函数，可单测）；
// 真实/mock 两种模式的 /api/jd/match 响应同构，因此都会出图。
const hoveredIndex = ref(null)
const radarData = computed(() => buildRadarData(result.value))
const radarAriaLabel = computed(() => {
  if (!radarData.value) return 'JD 匹配能力雷达图：暂无能力项数据'
  const parts = radarData.value.dimensions.map((dimension) => `${dimension.label} ${dimension.value} 分`)
  return `JD 匹配能力雷达图（满分 10 分）：${parts.join('；')}`
})

const pipeline = [
  { step: '①', title: 'JD 结构化抽取', detail: '低温（0.2）调用，输出岗位 / 职责 / 能力项（JDStructured）' },
  { step: '②', title: '逐项对齐评分', detail: '简历能力项 × JD 能力项 → 匹配度（0–100）+ 差距清单' },
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
    result.value = await api.jdMatch({
      resume_text: resumeText.value.trim(),
      jd_text: jdText.value.trim(),
    }, { signal: localController.signal })
    hoveredIndex.value = null
    saveResume({ text: resumeText.value, source: 'paste' })
    phase.value = 'done'
    notice.value = '匹配完成：匹配度与差距清单仅供参考，建议结合人工判断。'
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
      <p class="eyebrow">模块 B · JD 匹配</p>
      <h2>看懂 JD 的潜台词：匹配度与差距清单</h2>
      <p class="lead">
        输入目标岗位 JD，后端先做结构化能力项抽取，再与结构化简历逐项对齐评分。
        两段式调用保证每一步可控、可分别评测——这是本项目 Prompt 策略的要点之一。
      </p>
      <div class="chip-row">
        <span class="chip">两段式调用</span>
        <span class="chip">0–100 匹配度</span>
        <span class="chip">差距清单</span>
        <span v-if="isMockMode" class="chip chip-accent">Mock 数据模式</span>
        <span v-else class="chip chip-live">真实后端模式</span>
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
          <span v-if="busy" class="muted">匹配会调用后端模型，通常需要 10–90 秒。</span>
        </div>
      </div>

      <div class="stack">
        <div class="form-card">
          <h3 class="section-title">两段式调用</h3>
          <ul class="placeholder-list">
            <li v-for="item in pipeline" :key="item.title">
              <span class="idx">{{ item.step }}</span>
              <span><strong>{{ item.title }}</strong>：{{ item.detail }}</span>
            </li>
          </ul>
        </div>

        <template v-if="result">
          <div class="metric-grid">
            <div class="metric">
              <div class="label">匹配度</div>
              <div class="value">{{ result.score }}<small>/ 100</small></div>
            </div>
            <div class="metric">
              <div class="label">能力项</div>
              <div class="value">{{ result.items?.length ?? 0 }}<small>项</small></div>
            </div>
            <div class="metric">
              <div class="label">差距</div>
              <div class="value">{{ result.gaps?.length ?? 0 }}<small>项</small></div>
            </div>
          </div>

          <div class="form-card radar-card">
            <h3 class="section-title">能力雷达图（SVG · 无第三方库）</h3>
            <p class="muted">
              由 items 的 matched / partial / missing 与总分推导 5 个维度（规则见下方文本列表）；
              悬停顶点可看数值与推导规则，文本列表是无障碍等价信息（aria-label 同步提供整体描述）。
            </p>
            <div v-if="radarData" class="radar-layout">
              <svg class="radar-svg" :viewBox="radarData.viewBox" role="img" :aria-label="radarAriaLabel">
                <title>JD 匹配能力雷达图</title>
                <!-- 网格环（20% / 40% / 60% / 80% / 100%） -->
                <polygon
                  v-for="ring in radarData.rings"
                  :key="`ring-${ring.level}`"
                  :points="ring.points"
                  class="radar-grid"
                />
                <!-- 轴线 -->
                <line
                  v-for="(axis, index) in radarData.axes"
                  :key="`axis-${index}`"
                  :x1="radarData.center.x"
                  :y1="radarData.center.y"
                  :x2="axis.x"
                  :y2="axis.y"
                  class="radar-axis"
                />
                <!-- 数据多边形 -->
                <polygon :points="radarData.polygon" class="radar-area" />
                <!-- 数据点 + 轴标签（标签内含数值） -->
                <g
                  v-for="(dimension, index) in radarData.dimensions"
                  :key="dimension.key"
                  class="radar-group"
                  :class="{ 'is-active': hoveredIndex === index }"
                  @mouseenter="hoveredIndex = index"
                  @mouseleave="hoveredIndex = null"
                >
                  <circle
                    :cx="radarData.points[index].x"
                    :cy="radarData.points[index].y"
                    r="16"
                    class="radar-hit"
                  />
                  <circle
                    :cx="radarData.points[index].x"
                    :cy="radarData.points[index].y"
                    :r="hoveredIndex === index ? 5.5 : 4"
                    class="radar-dot"
                  >
                    <title>{{ dimension.label }}：{{ dimension.value }} / 10（{{ dimension.rule }}）</title>
                  </circle>
                  <text
                    :x="radarData.labels[index].x"
                    :y="radarData.labels[index].y"
                    :text-anchor="radarData.labels[index].anchor"
                    class="radar-axis-label"
                  >
                    {{ dimension.label }}<tspan class="radar-axis-value">{{ dimension.value.toFixed(1) }}</tspan>
                  </text>
                </g>
              </svg>

              <div class="radar-side">
                <ul class="radar-values" aria-label="雷达图数值（等价文本）">
                  <li
                    v-for="(dimension, index) in radarData.dimensions"
                    :key="`value-${dimension.key}`"
                    :class="{ 'is-active': hoveredIndex === index }"
                    @mouseenter="hoveredIndex = index"
                    @mouseleave="hoveredIndex = null"
                  >
                    <span class="radar-name">{{ dimension.label }}</span>
                    <span class="radar-score">{{ dimension.value.toFixed(1) }} / 10</span>
                    <span class="radar-rule">{{ dimension.rule }}</span>
                  </li>
                </ul>
                <p class="muted radar-summary">
                  样本：{{ radarData.meta.total }} 个能力项（matched {{ radarData.meta.matched }} /
                  partial {{ radarData.meta.partial }} / missing {{ radarData.meta.missing }}），
                  总分 {{ radarData.meta.score ?? '--' }} / 100。
                </p>
              </div>
            </div>
            <div v-else class="placeholder-block">
              本次匹配结果缺少能力项明细（items 为空），暂无法绘制雷达图；可参考下方匹配概览与差距清单文本。
            </div>
          </div>

          <div class="form-card">
            <h3 class="section-title">匹配概览</h3>
            <p class="muted">{{ result.summary }}</p>
            <ul class="placeholder-list">
              <li v-for="item in result.items" :key="item.capability">
                <span class="idx">·</span>
                <span>
                  <strong>{{ item.capability }}</strong>
                  <span class="badge" :data-status="item.status">{{ STATUS_LABELS[item.status] || item.status }}</span>
                  <br />{{ item.evidence }} —— {{ item.comment }}
                </span>
              </li>
            </ul>
          </div>

          <div class="form-card">
            <h3 class="section-title">差距清单</h3>
            <ul class="placeholder-list">
              <li v-for="gap in result.gaps" :key="gap.capability">
                <span class="idx">✗</span>
                <span><strong>{{ gap.capability }}</strong>：{{ gap.gap }}<br />建议：{{ gap.suggestion }}</span>
              </li>
            </ul>
          </div>
        </template>
        <div v-else class="form-card">
          <div class="placeholder-block">点击「开始匹配」后，在此展示匹配度 + 能力项覆盖 + 差距清单</div>
        </div>
      </div>
    </div>
  </section>
</template>
