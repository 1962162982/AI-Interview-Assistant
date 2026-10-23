<script setup>
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { clearHistory, formatTimestamp, loadHistory } from '../stores/history'

const scenes = ['实习', '秋招', '保研']

const modules = [
  {
    tag: '01',
    title: '简历诊断',
    path: '/resume',
    desc: '上传或粘贴简历，按 5 项检查清单定位问题，给出原文证据与改写建议',
  },
  {
    tag: '02',
    title: 'JD 匹配',
    path: '/jd',
    desc: '对照目标岗位要求逐项分析，看到 0–100 匹配分、能力差距与补齐建议',
  },
  {
    tag: '03',
    title: '模拟面试',
    path: '/interview',
    desc: '实习 / 秋招 / 保研三场景，多轮追问 + 逐字流式对话，像真实面试一样练习',
  },
  {
    tag: '04',
    title: '反馈报告',
    path: '/report',
    desc: '4 个维度评分 + 原文证据 + 交叉复核，生成可导出的面试反馈报告',
  },
]

const historyState = ref(loadHistory())

function refreshHistory() {
  historyState.value = loadHistory()
}

function clearAllHistory() {
  clearHistory()
  refreshHistory()
}

onMounted(() => {
  refreshHistory()
})
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">AI 求职练习</p>
      <h2>从简历到面试反馈，一条完整的练习链路</h2>
      <p class="lead">
        上传简历、粘贴目标岗位 JD，系统带你完成「简历诊断 — JD 匹配 — 模拟面试 — 反馈报告」四步，
        每一步都能看到具体的问题、证据与改进建议。
      </p>
      <div class="chip-row">
        <span v-for="scene in scenes" :key="scene" class="chip">{{ scene }}场景</span>
        <span class="chip">会追问的模拟面试</span>
        <span class="chip">可解释的评分报告</span>
      </div>
    </header>

    <div class="card-grid">
      <RouterLink v-for="item in modules" :key="item.tag" :to="item.path" class="card card-link">
        <span class="module-tag">{{ item.tag }}</span>
        <h3>{{ item.title }}</h3>
        <p>{{ item.desc }}</p>
        <span class="link-hint">进入模块 →</span>
      </RouterLink>
    </div>

    <div class="panel">
      <div>
        <h3>本地历史记录</h3>
        <p class="muted">
          自动保存最近一次简历、诊断报告、面试会话与反馈报告，仅存在本机浏览器，可随时一键清空。
        </p>
      </div>
      <div class="panel-actions">
        <button class="button ghost" @click="refreshHistory">刷新历史</button>
        <button class="button ghost" @click="clearAllHistory">清空历史</button>
      </div>
    </div>

    <div class="split-grid">
      <div class="form-card">
        <h3 class="section-title">最近简历</h3>
        <p v-if="historyState.resume" class="muted">
          来源：{{ historyState.resume.source === 'upload' ? '文件上传' : '粘贴文本' }} ·
          {{ formatTimestamp(historyState.resume.savedAt) }} · {{ historyState.resume.text.length }} 字
        </p>
        <pre v-if="historyState.resume" class="markdown-preview">{{ historyState.resume.text.slice(0, 320) }}{{ historyState.resume.text.length > 320 ? '\n…' : '' }}</pre>
        <p v-else class="muted">暂无：在「简历诊断」页上传文件或粘贴文本后自动保存。</p>
        <div v-if="historyState.resumeDiagnosis" class="panel-actions">
          <RouterLink class="button ghost" to="/resume-report">查看最近诊断报告 →</RouterLink>
        </div>
      </div>

      <div class="form-card">
        <h3 class="section-title">面试会话与报告</h3>
        <ul v-if="historyState.sessions.length" class="placeholder-list">
          <li v-for="item in historyState.sessions" :key="item.sessionId">
            <span class="idx">{{ item.scene }}</span>
            <span>
              <code>{{ item.sessionId }}</code><br />
              {{ formatTimestamp(item.createdAt) }} ·
              <RouterLink class="link-inline" :to="{ path: '/report', query: { session: item.sessionId } }">生成报告</RouterLink>
            </span>
          </li>
        </ul>
        <p v-else class="muted">暂无面试会话：在「模拟面试」页开始面试后自动记录。</p>

        <h4 class="sub-title">历史报告</h4>
        <ul v-if="historyState.reports.length" class="placeholder-list">
          <li v-for="item in historyState.reports" :key="item.reportId">
            <span class="idx">报告</span>
            <span>
              <code>{{ item.reportId }}</code><br />
              {{ formatTimestamp(item.createdAt) }} ·
              <RouterLink class="link-inline" :to="{ path: '/report', query: { report: item.reportId } }">查看</RouterLink>
            </span>
          </li>
        </ul>
        <p v-else class="muted">暂无历史报告：在报告页生成后将自动记录。</p>
      </div>
    </div>
  </section>
</template>
