<script setup>
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, health, isMockMode } from '../api/client'
import { clearHistory, formatTimestamp, loadHistory } from '../stores/history'

const scenes = ['实习', '秋招', '保研']

const modules = [
  {
    tag: 'A',
    title: '简历诊断',
    path: '/resume',
    desc: '解析 → 脱敏 → 结构化抽取 → 5 项检查清单 → 问题清单与改写示例',
  },
  {
    tag: 'B',
    title: 'JD 匹配',
    path: '/jd',
    desc: '两段式调用：JD 能力项抽取 → 逐项对齐评分 → 匹配度 + 差距清单',
  },
  {
    tag: 'C',
    title: '模拟面试',
    path: '/interview',
    desc: '角色卡 + 追问决策树 + 实习/秋招/保研三场景，多轮流式对话',
  },
  {
    tag: 'D',
    title: '反馈报告',
    path: '/report',
    desc: '4 维度 rubric + 强制引用证据 + 交叉复核 + 报告导出',
  },
]

const backendStatus = ref({ state: 'idle', message: '尚未检测' })
const routes = ref([])
const routesLoading = ref(false)
const routesError = ref('')
const historyState = ref(loadHistory())

async function checkBackend() {
  backendStatus.value = { state: 'loading', message: '检测中…' }
  try {
    const data = await health()
    backendStatus.value = {
      state: 'ok',
      message: data?.status === 'ok' ? '后端在线（GET /health 正常）' : `后端已响应：${JSON.stringify(data)}`,
    }
  } catch (error) {
    backendStatus.value = { state: 'error', message: error.message }
  }
}

async function loadRoutes() {
  routesLoading.value = true
  routesError.value = ''
  try {
    const data = await api.metaRoutes()
    routes.value = data?.routes ?? []
  } catch (error) {
    routesError.value = error.message
  } finally {
    routesLoading.value = false
  }
}

function refreshHistory() {
  historyState.value = loadHistory()
}

function clearAllHistory() {
  clearHistory()
  refreshHistory()
}

onMounted(() => {
  loadRoutes()
  refreshHistory()
})
</script>

<template>
  <section class="page">
    <header class="page-header">
      <p class="eyebrow">课程项目 · Web 应用</p>
      <h2>从简历到面试反馈，一条可演示的 AI 求职练习链路</h2>
      <p class="lead">
        学生提供简历与目标岗位 JD，系统完成「简历诊断 — JD 匹配 — 模拟面试 — 即时反馈」四步，
        最终产出可解释、可回溯的面试反馈报告。技术路线为纯 Prompt 工程，不涉及模型微调。
      </p>
      <div class="chip-row">
        <span v-for="scene in scenes" :key="scene" class="chip">{{ scene }}场景</span>
        <span class="chip">四层架构</span>
        <span class="chip">每模块独立模型</span>
        <span class="chip">评测回归关卡</span>
        <span v-if="isMockMode" class="chip chip-accent">Mock 数据模式</span>
        <span v-else class="chip chip-live">真实后端模式</span>
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
        <h3>模块 → 模型路由（GET /api/meta/routes）</h3>
        <p class="muted">
          后端返回 5 条路由的非敏感摘要（模型 / 温度 / 流式 / 超时 / 重试），
          前端据此自查决策④「每模块独立模型」是否生效。
        </p>
      </div>
      <div class="panel-actions">
        <button class="button ghost" :disabled="routesLoading" @click="loadRoutes">
          {{ routesLoading ? '加载中…' : '刷新路由表' }}
        </button>
        <span v-if="routesError" class="status" data-state="error">{{ routesError }}</span>
      </div>
    </div>

    <div v-if="routes.length" class="form-card table-card">
      <table class="data-table">
        <thead>
          <tr>
            <th>路由（模块）</th>
            <th>模型</th>
            <th>温度</th>
            <th>流式</th>
            <th>超时 / 重试</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="route in routes" :key="route.route">
            <td><code>{{ route.route }}</code></td>
            <td>{{ route.model }}</td>
            <td>{{ route.temperature }}</td>
            <td>{{ route.stream ? '是' : '否' }}</td>
            <td>{{ route.timeout }}s / {{ route.max_retries }} 次</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <div>
        <h3>后端连通性自检</h3>
        <p class="muted">
          调用 <code>GET /health</code> 确认后端进程在线；真实模式下所有页面均通过
          <code>src/api/client.js</code> 调用后端（契约见 <code>docs/api.md</code>）。
        </p>
      </div>
      <div class="panel-actions">
        <button class="button" :disabled="backendStatus.state === 'loading'" @click="checkBackend">
          {{ backendStatus.state === 'loading' ? '检测中…' : '检测后端' }}
        </button>
        <span class="status" :data-state="backendStatus.state">{{ backendStatus.message }}</span>
      </div>
    </div>

    <div class="panel">
      <div>
        <h3>本地历史记录（localStorage）</h3>
        <p class="muted">
          自动保存最近一次简历文本、面试会话（session_id）与报告（report_id），仅存在本机浏览器；
          刷新页面或重新演示时可复用，也可一键清空。
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
