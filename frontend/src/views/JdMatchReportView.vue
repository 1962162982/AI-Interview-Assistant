<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { buildRadarData } from '../utils/radar'
import { formatTimestamp, getJdMatch } from '../stores/history'

// 独立的「JD 匹配报告」页面：匹配成功后由 JdMatchView 保存结果并跳转到这里，
// 用整页宽度展示匹配度、能力雷达图、逐项对比与差距清单。
const router = useRouter()
const payload = getJdMatch()
const result = payload?.result || null

const STATUS_LABELS = { matched: '已覆盖', partial: '部分覆盖', missing: '缺失' }
const hoveredIndex = ref(null)

const radarData = computed(() => buildRadarData(result))
const radarAriaLabel = computed(() => {
  if (!radarData.value) return 'JD 匹配能力雷达图：暂无能力项数据'
  const parts = radarData.value.dimensions.map((dimension) => `${dimension.label} ${dimension.value} 分`)
  return `JD 匹配能力雷达图（满分 10 分）：${parts.join('；')}`
})
</script>

<template>
  <section class="page report-page">
    <header class="page-header">
      <p class="eyebrow">JD 匹配报告</p>
      <h2>你与目标岗位的匹配报告</h2>
      <p class="lead">
        从匹配度、能力覆盖与差距三个角度看简历与岗位的匹配情况；结果仅供参考，建议结合真实岗位理解与人工判断。
      </p>
      <div v-if="result" class="report-meta">
        <span>生成时间：{{ formatTimestamp(payload.savedAt) }}</span>
        <span>简历 {{ payload.resumeText?.length || 0 }} 字 · JD {{ payload.jdText?.length || 0 }} 字</span>
      </div>
      <div class="panel-actions">
        <button class="button" @click="router.push('/jd')">重新匹配</button>
        <button class="button ghost" @click="router.push('/interview')">去模拟面试</button>
      </div>
    </header>

    <div v-if="!result" class="form-card">
      <h3 class="section-title">还没有匹配结果</h3>
      <p class="muted">请先在「JD 匹配」页填入目标岗位 JD 并点击「开始匹配」。</p>
      <div class="panel-actions">
        <button class="button" @click="router.push('/jd')">去 JD 匹配</button>
      </div>
    </div>

    <template v-else>
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
        <h3 class="section-title">能力雷达图</h3>
        <p class="muted">
          从 5 个维度展示你与岗位要求的匹配情况；悬停顶点可看数值与计算口径，右侧文本列表为等价信息。
        </p>
        <div v-if="radarData" class="radar-layout">
          <svg class="radar-svg" :viewBox="radarData.viewBox" role="img" :aria-label="radarAriaLabel">
            <title>JD 匹配能力雷达图</title>
            <polygon
              v-for="ring in radarData.rings"
              :key="`ring-${ring.level}`"
              :points="ring.points"
              class="radar-grid"
            />
            <line
              v-for="(axis, index) in radarData.axes"
              :key="`axis-${index}`"
              :x1="radarData.center.x"
              :y1="radarData.center.y"
              :x2="axis.x"
              :y2="axis.y"
              class="radar-axis"
            />
            <polygon :points="radarData.polygon" class="radar-area" />
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
              本次共 {{ radarData.meta.total }} 个能力项（已覆盖 {{ radarData.meta.matched }} ·
              部分覆盖 {{ radarData.meta.partial }} · 缺失 {{ radarData.meta.missing }}），
              总体匹配度 {{ radarData.meta.score ?? '--' }} / 100。
            </p>
          </div>
        </div>
        <div v-else class="placeholder-block">
          本次匹配结果缺少能力项明细，暂无法绘制雷达图；可参考下方匹配概览与差距清单。
        </div>
      </div>

      <div class="form-card">
        <h3 class="section-title">匹配概览</h3>
        <p class="muted">{{ result.summary }}</p>
        <ul class="plain-list capability-list">
          <li v-for="item in result.items" :key="item.capability">
            <strong>{{ item.capability }}</strong>
            <span class="badge" :data-status="item.status">{{ STATUS_LABELS[item.status] || item.status }}</span>
            <span class="capability-detail">{{ item.evidence }} —— {{ item.comment }}</span>
          </li>
        </ul>
      </div>

      <div class="form-card">
        <h3 class="section-title">差距清单</h3>
        <p class="muted">以下能力项是岗位要求中你目前较薄弱或缺失的部分，可按建议补齐：</p>
        <ul class="plain-list gap-list">
          <li v-for="gap in result.gaps" :key="gap.capability">
            <strong>{{ gap.capability }}</strong>
            <span class="capability-detail">{{ gap.gap }} → 建议：{{ gap.suggestion }}</span>
          </li>
        </ul>
      </div>

      <div class="form-card">
        <h3 class="section-title">下一步</h3>
        <p class="muted">可以带着这份匹配结果去模拟面试，面试官会结合岗位要求追问你的相关经历。</p>
        <div class="panel-actions">
          <button class="button" @click="router.push('/interview')">去模拟面试</button>
          <button class="button ghost" @click="router.push('/jd')">重新匹配</button>
        </div>
      </div>
    </template>
  </section>
</template>
