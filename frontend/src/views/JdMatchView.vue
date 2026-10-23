<script setup>
import { computed, ref } from 'vue'
import { api, isMockMode } from '../api/client'
import { SAMPLE_JD_TEXT, SAMPLE_RESUME_TEXT } from '../api/mock'

// 模块 B JD 匹配：按 docs/api.md 契约调用 /api/jd/match（两段式由后端编排）。
const STATUS_LABELS = { matched: '已覆盖', partial: '部分覆盖', missing: '缺失' }

const jdText = ref('')
const phase = ref('idle') // idle | matching | done
const errorMessage = ref('')
const result = ref(null)

const busy = computed(() => phase.value === 'matching')

const pipeline = [
  { step: '①', title: 'JD 结构化抽取', detail: '低温（0.2）调用，输出岗位 / 职责 / 能力项（JDStructured）' },
  { step: '②', title: '逐项对齐评分', detail: '简历能力项 × JD 能力项 → 匹配度（0–100）+ 差距清单' },
]

function fillSample() {
  jdText.value = SAMPLE_JD_TEXT
}

async function match() {
  if (!jdText.value.trim()) {
    errorMessage.value = '请先输入目标岗位 JD 文本'
    return
  }
  phase.value = 'matching'
  errorMessage.value = ''
  try {
    result.value = await api.jdMatch({
      resume_text: SAMPLE_RESUME_TEXT,
      jd_text: jdText.value.trim(),
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
      </div>
    </header>

    <div class="split-grid">
      <div class="form-card">
        <div class="field">
          <label for="jd-text">目标岗位 JD 文本</label>
          <textarea
            id="jd-text"
            v-model="jdText"
            class="textarea"
            rows="12"
            placeholder="粘贴岗位 JD，或点击下方「填入示例 JD」"
          ></textarea>
          <span class="hint">简历侧使用示例简历（{{ SAMPLE_RESUME_TEXT.length }} 字）参与匹配；真实联调时由简历诊断页传入。</span>
        </div>
        <div class="panel-actions">
          <button class="button" :disabled="busy" @click="match">
            {{ busy ? '匹配中…' : '开始匹配' }}
          </button>
          <button class="button ghost" :disabled="busy" @click="fillSample">填入示例 JD</button>
          <span v-if="errorMessage" class="status" data-state="error">{{ errorMessage }}</span>
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
